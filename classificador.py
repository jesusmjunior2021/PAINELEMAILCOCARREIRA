"""
classificador.py — atribui NATUREZA, TEMA e ESTADO_DEMANDA a cada registro.

Duas fontes, nesta ordem de precedência:
  1. LABEL do Gmail (aba DE_PARA_LABELS) — fonte de verdade, organizada
     manualmente pela equipe.
  2. REGRA determinística (aba REGRAS_CLASSIFICACAO) — só entra quando o
     label não resolveu aquele eixo.

Nada aqui é IA nem heurística probabilística: toda atribuição vem de uma
linha de parâmetro que um humano escreveu na planilha, e o registro guarda
em REGRA_APLICADA exatamente qual linha o classificou. Se nada bater, o
valor é NAO_CLASSIFICADO e ORIGEM_CLASSIFICACAO é NENHUMA — nunca chute.

Os três eixos são independentes: uma regra pode definir só a NATUREZA
(ex.: remetente cocarreira@ = RESPOSTA_NOSSA) sem impedir que outra regra,
de ordem posterior, defina o TEMA.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

EIXOS = ["NATUREZA", "TEMA", "ESTADO_DEMANDA"]
NAO_CLASSIFICADO = "NAO_CLASSIFICADO"

# NATUREZAs que não representam trabalho da Coordenadoria.
NATUREZAS_RUIDO = {
    "PUBLICIDADE_EXTERNA",
    "DIVULGACAO_INSTITUCIONAL",
    "NOTIFICACAO_SISTEMA",
}

SEPARADOR_LABEL = "|"

# Extração de número de processo/requisição/chamado a partir do assunto.
# Só captura quando há uma palavra-âncora antes do número — evita pegar
# data, valor ou número de turma.
PADRAO_NUMERO_PROCESSO = re.compile(
    r"(?:processo|requisi[çc][ãa]o|digidoc|chamado|protocolo|"
    r"pedido\s+de\s+informa[çc][ãa]o)"
    r"[^0-9]{0,20}(\d[\d./\-]{3,})",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Normalização dos parâmetros
# ---------------------------------------------------------------------------

def _texto(valor) -> str:
    return "" if valor is None else str(valor).strip()


def preparar_de_para_labels(df: pd.DataFrame) -> Dict[Tuple[str, str], Tuple[str, str]]:
    """
    Devolve {(label, eixo): (valor, rotulo_da_regra)}.
    Linhas com ATIVO != SIM são ignoradas.
    """
    mapa: Dict[Tuple[str, str], Tuple[str, str]] = {}
    if df is None or df.empty:
        return mapa
    for _, linha in df.iterrows():
        if _texto(linha.get("ATIVO")).upper() not in ("SIM", "S", "TRUE", "1"):
            continue
        label = _texto(linha.get("LABEL_GMAIL"))
        eixo = _texto(linha.get("EIXO")).upper()
        valor = _texto(linha.get("VALOR"))
        if not label or not valor:
            continue
        if eixo == "ESTADO":  # apelido curto usado na planilha
            eixo = "ESTADO_DEMANDA"
        if eixo not in EIXOS:
            continue
        mapa[(label, eixo)] = (valor, f"LABEL:{label}")
    return mapa


def preparar_regras(df: pd.DataFrame) -> List[Dict[str, str]]:
    """Lista de regras ativas, ordenada por ORDEM crescente."""
    if df is None or df.empty:
        return []
    regras: List[Dict[str, str]] = []
    for posicao, linha in df.iterrows():
        if _texto(linha.get("ATIVA")).upper() not in ("SIM", "S", "TRUE", "1"):
            continue
        tipo = _texto(linha.get("TIPO_REGRA")).upper()
        padrao = _texto(linha.get("PADRAO"))
        if not tipo or not padrao:
            continue
        try:
            ordem = int(float(_texto(linha.get("ORDEM")) or 999))
        except ValueError:
            ordem = 999
        regra = {
            "ordem": ordem,
            "tipo": tipo,
            "padrao": padrao,
            "NATUREZA": _texto(linha.get("NATUREZA")),
            "TEMA": _texto(linha.get("TEMA")),
            "ESTADO_DEMANDA": _texto(linha.get("ESTADO_SUGERIDO")),
            "rotulo": f"REGRA:{ordem}:{tipo}:{padrao[:40]}",
        }
        if regra["tipo"] == "ASSUNTO_REGEX":
            try:
                regra["compilado"] = re.compile(padrao, re.IGNORECASE)
            except re.error:
                # Regex inválida escrita na planilha não derruba o app:
                # a regra é descartada e sinalizada.
                regra["compilado"] = None
                regra["rotulo"] += " (REGEX INVÁLIDA — IGNORADA)"
                regras.append(regra)
                continue
        regras.append(regra)
    return sorted(regras, key=lambda r: (r["ordem"], r["rotulo"]))


def regras_invalidas(regras: List[Dict[str, str]]) -> List[str]:
    return [r["rotulo"] for r in regras if r["tipo"] == "ASSUNTO_REGEX" and r.get("compilado") is None]


# ---------------------------------------------------------------------------
# Casamento
# ---------------------------------------------------------------------------

def _regra_casa(regra: Dict[str, str], assunto: str, remetente: str, dominio: str) -> bool:
    tipo, padrao = regra["tipo"], regra["padrao"]
    if tipo == "REMETENTE":
        return remetente == padrao.lower()
    if tipo == "REMETENTE_CONTEM":
        return padrao.lower() in remetente
    if tipo == "DOMINIO":
        alvo = padrao.lower().lstrip("@")
        return dominio == alvo or dominio.endswith("." + alvo)
    if tipo == "ASSUNTO_CONTEM":
        return padrao.lower() in assunto
    if tipo == "ASSUNTO_REGEX":
        compilado = regra.get("compilado")
        return bool(compilado and compilado.search(assunto))
    return False


def _labels_da_linha(valor: str) -> List[str]:
    return [p.strip() for p in _texto(valor).split(SEPARADOR_LABEL) if p.strip()]


def classificar_linha(
    linha: pd.Series,
    mapa_labels: Dict[Tuple[str, str], Tuple[str, str]],
    regras: List[Dict[str, str]],
) -> Dict[str, str]:
    resultado = {eixo: "" for eixo in EIXOS}
    origem = {eixo: "" for eixo in EIXOS}
    evidencias: List[str] = []

    # 1) Labels do Gmail
    for label in _labels_da_linha(linha.get("LABELS_GMAIL", "")):
        for eixo in EIXOS:
            if resultado[eixo]:
                continue
            achado = mapa_labels.get((label, eixo))
            if achado:
                resultado[eixo], rotulo = achado
                origem[eixo] = "LABEL"
                evidencias.append(f"{eixo}<-{rotulo}")

    # 2) Regras determinísticas, só para os eixos ainda vazios
    if not all(resultado[e] for e in EIXOS):
        assunto = _texto(linha.get("ASSUNTO", "")).lower()
        remetente = _texto(linha.get("REMETENTE_EMAIL", "")).lower()
        dominio = remetente.split("@")[-1] if "@" in remetente else ""
        for regra in regras:
            if all(resultado[e] for e in EIXOS):
                break
            if not _regra_casa(regra, assunto, remetente, dominio):
                continue
            for eixo in EIXOS:
                if not resultado[eixo] and regra[eixo]:
                    resultado[eixo] = regra[eixo]
                    origem[eixo] = "REGRA"
                    evidencias.append(f"{eixo}<-{regra['rotulo']}")

    for eixo in EIXOS:
        if not resultado[eixo]:
            resultado[eixo] = NAO_CLASSIFICADO

    origens_usadas = sorted({v for v in origem.values() if v})
    return {
        **resultado,
        "ORIGEM_CLASSIFICACAO": "+".join(origens_usadas) if origens_usadas else "NENHUMA",
        "REGRA_APLICADA": " ; ".join(evidencias),
    }


def extrair_numero_processo(assunto: str) -> str:
    achado = PADRAO_NUMERO_PROCESSO.search(_texto(assunto))
    return achado.group(1).strip(" ./-") if achado else ""


# ---------------------------------------------------------------------------
# API principal
# ---------------------------------------------------------------------------

def classificar(
    df: pd.DataFrame,
    df_de_para: Optional[pd.DataFrame],
    df_regras: Optional[pd.DataFrame],
    respeitar_existente: bool = True,
) -> pd.DataFrame:
    """
    Devolve cópia de `df` com as colunas de classificação acrescentadas.

    `respeitar_existente=True` (padrão): se o Apps Script v2 já gravou
    NATUREZA/TEMA/ESTADO_DEMANDA na planilha, esses valores são MANTIDOS e o
    app só classifica as linhas ainda vazias. Evita que planilha e app cheguem
    a resultados diferentes para o mesmo registro — e evita reprocessar o que
    já foi processado.
    """
    resultado = df.copy()
    ja_classificado = None
    if respeitar_existente and all(c in resultado.columns for c in EIXOS):
        preenchidas = pd.Series(True, index=resultado.index)
        for eixo in EIXOS:
            valores = resultado[eixo].fillna("").astype(str).str.strip()
            preenchidas &= (valores != "") & (valores != NAO_CLASSIFICADO)
        if preenchidas.any():
            ja_classificado = resultado.loc[preenchidas].copy()
            resultado = resultado.loc[~preenchidas].copy()
    mapa_labels = preparar_de_para_labels(df_de_para)
    regras = preparar_regras(df_regras)

    if resultado.empty:
        for coluna in EIXOS + ["ORIGEM_CLASSIFICACAO", "REGRA_APLICADA", "NUMERO_PROCESSO", "E_RUIDO"]:
            if coluna not in resultado.columns:
                resultado[coluna] = pd.Series(dtype=str)
        if ja_classificado is not None and not ja_classificado.empty:
            return _finalizar_existentes(ja_classificado)
        return resultado

    classificadas = resultado.apply(
        lambda linha: classificar_linha(linha, mapa_labels, regras), axis=1, result_type="expand"
    )
    for coluna in classificadas.columns:
        resultado[coluna] = classificadas[coluna]

    resultado["NUMERO_PROCESSO"] = resultado["ASSUNTO"].map(extrair_numero_processo)
    resultado["E_RUIDO"] = resultado["NATUREZA"].isin(NATUREZAS_RUIDO).map({True: "SIM", False: "NAO"})

    if ja_classificado is not None and not ja_classificado.empty:
        for coluna, padrao in [
            ("ORIGEM_CLASSIFICACAO", "PLANILHA"),
            ("REGRA_APLICADA", "Classificado pelo Apps Script v2"),
        ]:
            if coluna not in ja_classificado.columns:
                ja_classificado[coluna] = padrao
            else:
                vazios = ja_classificado[coluna].fillna("").astype(str).str.strip() == ""
                ja_classificado.loc[vazios, coluna] = padrao
        if "NUMERO_PROCESSO" not in ja_classificado.columns:
            ja_classificado["NUMERO_PROCESSO"] = ja_classificado["ASSUNTO"].map(extrair_numero_processo)
        ja_classificado["E_RUIDO"] = (
            ja_classificado["NATUREZA"].isin(NATUREZAS_RUIDO).map({True: "SIM", False: "NAO"})
        )
        resultado = pd.concat([ja_classificado, resultado]).sort_index()

    return resultado


def _finalizar_existentes(ja_classificado: pd.DataFrame) -> pd.DataFrame:
    """Completa colunas de apoio para linhas que já vieram classificadas da planilha."""
    df = ja_classificado.copy()
    if "ORIGEM_CLASSIFICACAO" not in df.columns:
        df["ORIGEM_CLASSIFICACAO"] = "PLANILHA"
    if "REGRA_APLICADA" not in df.columns:
        df["REGRA_APLICADA"] = "Classificado pelo Apps Script v2"
    if "NUMERO_PROCESSO" not in df.columns:
        df["NUMERO_PROCESSO"] = df["ASSUNTO"].map(extrair_numero_processo)
    df["E_RUIDO"] = df["NATUREZA"].isin(NATUREZAS_RUIDO).map({True: "SIM", False: "NAO"})
    return df


def cobertura(df: pd.DataFrame) -> Dict[str, float]:
    """Percentual de registros com cada eixo resolvido — para o Diagnóstico."""
    total = max(len(df), 1)
    medidas = {
        eixo: round(100 * (df[eixo] != NAO_CLASSIFICADO).sum() / total, 1) for eixo in EIXOS
    }
    medidas["por_label"] = round(
        100 * df["ORIGEM_CLASSIFICACAO"].str.contains("LABEL").sum() / total, 1
    )
    return medidas
