"""
motor_prazos.py — cálculo de prazo dirigido por parâmetro, nunca por código.

Princípio: **só existe prazo onde a aba PARAMETROS_PRAZO declara prazo**,
com base normativa citada. Tema sem linha ATIVA sai como
SEM_PRAZO_PARAMETRIZADO; tema com regra mas sem data-base legível sai como
SEM_DATA_BASE. Em nenhuma hipótese o app estima uma data.

Não substitui o STATUS_PRAZO_ART25 do Auxílio Bolsa, que continua sendo
calculado pelo Apps Script e permanece a referência oficial daquele fluxo.
Aqui ele é apenas reproduzido para o painel unificado de prazos.

LIMITAÇÃO CONHECIDA — dias ÚTEIS: a contagem exclui sábados e domingos,
mas só exclui feriados se uma lista for informada em `feriados`. Enquanto
não houver calendário de feriados forenses do TJMA cadastrado, prazos em
dias úteis devem ser lidos como ESTIMATIVA OTIMISTA e a UI avisa isso.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

VENCIDO = "VENCIDO"
VENCENDO = "VENCENDO"
DENTRO_DO_PRAZO = "DENTRO_DO_PRAZO"
SEM_PRAZO_PARAMETRIZADO = "SEM_PRAZO_PARAMETRIZADO"
SEM_DATA_BASE = "SEM_DATA_BASE"

ROTULO_SITUACAO: Dict[str, str] = {
    VENCIDO: "Vencido",
    VENCENDO: "Vencendo",
    DENTRO_DO_PRAZO: "Dentro do prazo",
    SEM_PRAZO_PARAMETRIZADO: "Sem prazo parametrizado",
    SEM_DATA_BASE: "Sem data-base legível",
}

COR_SITUACAO: Dict[str, str] = {
    VENCIDO: "#B3261E",
    VENCENDO: "#E8710A",
    DENTRO_DO_PRAZO: "#1E7B34",
    SEM_PRAZO_PARAMETRIZADO: "#6B6B6B",
    SEM_DATA_BASE: "#6B6B6B",
}

CHAVE_SLA = "__SLA_INTERNO__"
LIMIAR_PADRAO = 5

# Naturezas que podem receber o SLA interno de resposta.
NATUREZAS_COM_SLA = {"DEMANDA_INTERNA", "DEMANDA_EXTERNA"}

# EVENTO_INICIAL -> coluna de origem da data-base.
COLUNA_DO_EVENTO: Dict[str, str] = {
    "DATA_HORA_ENVIO": "DATA_HORA_ENVIO",
    "DATA_TERMINO_CURSO": "DATA_TERMINO_CURSO",
    "DATA_EVENTO_MANUAL": "DATA_EVENTO_MANUAL",
}


def _texto(valor) -> str:
    return "" if valor is None else str(valor).strip()


def _inteiro(valor) -> Optional[int]:
    texto = _texto(valor)
    if not texto:
        return None
    try:
        return int(float(texto))
    except ValueError:
        return None


def preparar_parametros(df: Optional[pd.DataFrame]) -> Dict[str, Dict]:
    """Devolve {TEMA: parâmetros} apenas para linhas ATIVO=SIM com DIAS preenchido."""
    parametros: Dict[str, Dict] = {}
    if df is None or df.empty:
        return parametros
    for _, linha in df.iterrows():
        if _texto(linha.get("ATIVO")).upper() not in ("SIM", "S", "TRUE", "1"):
            continue
        dias = _inteiro(linha.get("DIAS"))
        tema = _texto(linha.get("TEMA"))
        if not tema or dias is None:
            # ATIVO sem DIAS é configuração incompleta: ignorada de propósito,
            # para não produzir prazo sem número declarado.
            continue
        parametros[tema] = {
            "evento": _texto(linha.get("EVENTO_INICIAL")) or "DATA_HORA_ENVIO",
            "dias": dias,
            "tipo_dias": (_texto(linha.get("TIPO_DIAS")) or "CORRIDOS").upper(),
            "limiar": _inteiro(linha.get("LIMIAR_ALERTA_DIAS")) or LIMIAR_PADRAO,
            "base_normativa": _texto(linha.get("BASE_NORMATIVA")),
        }
    return parametros


def parametros_incompletos(df: Optional[pd.DataFrame]) -> List[str]:
    """Temas marcados ATIVO=SIM mas sem DIAS — erro de preenchimento a avisar."""
    if df is None or df.empty:
        return []
    pendentes = []
    for _, linha in df.iterrows():
        if _texto(linha.get("ATIVO")).upper() in ("SIM", "S", "TRUE", "1") and _inteiro(linha.get("DIAS")) is None:
            pendentes.append(_texto(linha.get("TEMA")))
    return pendentes


def _somar_dias(base: pd.Timestamp, dias: int, tipo: str, feriados: Optional[List[str]]) -> pd.Timestamp:
    if tipo == "UTEIS":
        resultado = np.busday_offset(
            np.datetime64(base.date(), "D"), dias, roll="forward",
            holidays=np.array(feriados or [], dtype="datetime64[D]"),
        )
        return pd.Timestamp(str(resultado))
    return base.normalize() + pd.Timedelta(days=dias)


def _data_base(linha: pd.Series, evento: str) -> Optional[pd.Timestamp]:
    coluna = COLUNA_DO_EVENTO.get(evento, "DATA_HORA_ENVIO")
    if coluna == "DATA_HORA_ENVIO" and "_DATA_ENVIO" in linha.index:
        valor = linha["_DATA_ENVIO"]
        return None if pd.isna(valor) else pd.Timestamp(valor)
    bruto = _texto(linha.get(coluna, ""))
    if not bruto or bruto.lower().startswith("não apur"):
        return None
    convertido = pd.to_datetime(bruto, errors="coerce", dayfirst=True)
    return None if pd.isna(convertido) else convertido


def calcular(
    df: pd.DataFrame,
    df_parametros: Optional[pd.DataFrame],
    hoje: Optional[pd.Timestamp] = None,
    feriados: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Acrescenta PRAZO_LIMITE, DIAS_RESTANTES, SITUACAO_PRAZO e BASE_PRAZO."""
    resultado = df.copy()
    parametros = preparar_parametros(df_parametros)
    sla = parametros.get(CHAVE_SLA)
    referencia = pd.Timestamp(hoje or pd.Timestamp.now()).normalize()

    limites, restantes, situacoes, bases = [], [], [], []

    for _, linha in resultado.iterrows():
        tema = _texto(linha.get("TEMA", ""))
        regra = parametros.get(tema)
        origem = f"PARAMETROS_PRAZO / {tema}"

        if regra is None and sla and _texto(linha.get("NATUREZA", "")) in NATUREZAS_COM_SLA:
            regra = sla
            origem = "PARAMETROS_PRAZO / meta interna de resposta (não normativa)"

        if regra is None:
            limites.append(""); restantes.append(None)
            situacoes.append(SEM_PRAZO_PARAMETRIZADO); bases.append("")
            continue

        base = _data_base(linha, regra["evento"])
        if base is None:
            limites.append(""); restantes.append(None)
            situacoes.append(SEM_DATA_BASE)
            bases.append(f"{origem} — falta {regra['evento']}")
            continue

        limite = _somar_dias(base, regra["dias"], regra["tipo_dias"], feriados)
        dias_restantes = int((limite.normalize() - referencia).days)
        if dias_restantes < 0:
            situacao = VENCIDO
        elif dias_restantes <= regra["limiar"]:
            situacao = VENCENDO
        else:
            situacao = DENTRO_DO_PRAZO

        limites.append(limite.strftime("%d/%m/%Y"))
        restantes.append(dias_restantes)
        situacoes.append(situacao)
        detalhe = f"{origem} — {regra['dias']} dia(s) {regra['tipo_dias'].lower()} a partir de {regra['evento']}"
        if regra["base_normativa"]:
            detalhe += f" · {regra['base_normativa']}"
        bases.append(detalhe)

    resultado["PRAZO_LIMITE"] = limites
    resultado["DIAS_RESTANTES"] = restantes
    resultado["SITUACAO_PRAZO"] = situacoes
    resultado["BASE_PRAZO"] = bases
    return resultado


def resumo(df: pd.DataFrame) -> Dict[str, int]:
    contagens = {codigo: 0 for codigo in ROTULO_SITUACAO}
    for codigo, quantidade in df["SITUACAO_PRAZO"].value_counts().items():
        contagens[codigo] = int(quantidade)
    return contagens
