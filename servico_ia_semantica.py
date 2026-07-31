"""
servico_ia_semantica.py — camada de enriquecimento por IA (Groq), em cima do
que classificador.py e motor_prazos.py já resolvem de forma determinística.

PRINCÍPIO QUE NÃO MUDA: NATUREZA, TEMA e ESTADO_DEMANDA continuam vindo só
de LABEL do Gmail ou REGRA da planilha (classificador.py). Este módulo NUNCA
escreve nessas 3 colunas nem em ORIGEM_CLASSIFICACAO/REGRA_APLICADA. O que ele
faz é diferente: para o que ainda está solto ou ambíguo, calcula uma SUGESTÃO
separada, sempre rotulada como IA — nunca fonte oficial.

TEORIA DE CONJUNTOS FUZZY, na prática: em vez do casamento binário do
classificador.py ("bateu a regra ou não bateu"), cada eixo é tratado aqui
como um conjunto fuzzy sobre os valores JÁ OBSERVADOS/PARAMETRIZADOS na
planilha (nunca uma lista fixa no código-fonte — os valores de TEMA vêm do
que já existe em DE_PARA_LABELS/REGRAS_CLASSIFICACAO, então um TEMA novo que
a equipe cadastrar amanhã já entra automaticamente no cálculo). Para cada
e-mail, o Groq devolve um grau de pertinência (0.0–1.0) do e-mail a CADA
valor do eixo. Só existe "sugestão" quando o maior grau ultrapassa
`limiar_sugestao` — abaixo disso, o e-mail fica sem sugestão de IA para
aquele eixo, mesmo que o eixo oficial (classificador.py) esteja
NAO_CLASSIFICADO. IA sem confiança suficiente não sugere nada; é melhor
ficar em branco do que empurrar um palpite fraco pro card.

Além da pertinência fuzzy por eixo, o mesmo call extrai (texto livre, sem
inferência): NUMERO_PROCESSO, PARTES_MENCIONADAS e DATAS_CITADAS_TEXTO — só
preenchidos aqui quando o campo determinístico correspondente está vazio.

Um único call por e-mail cobre tudo isso (cota liberada: não há mais motivo
para múltiplas chamadas fatiadas). Cache por ID_MENSAGEM evita reprocessar
o mesmo e-mail em reloads futuros.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

MODELO_GROQ = "llama-3.3-70b-versatile"

EIXOS_FUZZY = ["NATUREZA", "TEMA", "ESTADO_DEMANDA"]
LIMIAR_SUGESTAO_PADRAO = 0.6
CHAVE_SEM_VALOR = "(nenhum valor conhecido para comparar)"


# ---------------------------------------------------------------------------
# Modelo de dados
# ---------------------------------------------------------------------------

@dataclass
class AnaliseSemanticaEmail:
    id_mensagem: str
    graus: Dict[str, Dict[str, float]] = field(default_factory=dict)  # eixo -> {valor: grau}
    sugestao: Dict[str, Optional[str]] = field(default_factory=dict)   # eixo -> valor sugerido (ou None)
    grau_sugestao: Dict[str, float] = field(default_factory=dict)      # eixo -> grau do valor sugerido
    urgencia: Optional[float] = None
    resumo: str = ""
    providencia_sugerida: str = ""
    numero_processo: str = ""
    partes_mencionadas: str = ""
    datas_citadas_texto: str = ""
    erro: str = ""

    def sugestao_para_card(self, eixo: str) -> str:
        """Texto pronto pra exibir no card: 'TEMA sugerido (0.82)' ou vazio."""
        valor = self.sugestao.get(eixo)
        if not valor:
            return ""
        grau = self.grau_sugestao.get(eixo, 0)
        return f"{valor} ({grau:.2f})"


# ---------------------------------------------------------------------------
# Montagem do prompt — dinâmico, valores vêm da planilha, nunca hardcoded
# ---------------------------------------------------------------------------

def _bloco_valores_conhecidos(valores_por_eixo: Dict[str, List[str]]) -> str:
    linhas = []
    for eixo in EIXOS_FUZZY:
        valores = valores_por_eixo.get(eixo) or []
        valores = [v for v in valores if v and v != "NAO_CLASSIFICADO"]
        texto_valores = ", ".join(valores) if valores else CHAVE_SEM_VALOR
        linhas.append(f"- {eixo}: {texto_valores}")
    return "\n".join(linhas)


def _prompt_sistema(valores_por_eixo: Dict[str, List[str]]) -> str:
    bloco_valores = _bloco_valores_conhecidos(valores_por_eixo)
    return f"""Você analisa UM e-mail administrativo e devolve SOMENTE um JSON válido,
sem nenhum texto antes ou depois, no formato exato abaixo.

Os únicos valores possíveis para cada eixo são os já cadastrados pela equipe
(abaixo). NÃO invente um valor novo, mesmo que ache que se encaixaria melhor:
{bloco_valores}

Para cada eixo, devolva um grau de pertinência de 0.0 a 1.0 para CADA valor
listado, refletindo o quanto o conteúdo do e-mail se aproxima daquele valor
(0 = nada relacionado, 1 = certeza total). Não precisa somar 1 entre os
valores — cada grau é independente.

Formato exato de saída:
{{
  "graus": {{
    "NATUREZA": {{"valor_a": 0.0, "valor_b": 0.0}},
    "TEMA": {{"valor_a": 0.0}},
    "ESTADO_DEMANDA": {{"valor_a": 0.0}}
  }},
  "urgencia": 0.0,
  "resumo": "",
  "providencia_sugerida": "",
  "numero_processo": "",
  "partes_mencionadas": "",
  "datas_citadas_texto": ""
}}

Regras adicionais:
- "urgencia": 0.0 a 1.0, sua estimativa de quão urgente é a resposta/ação,
  baseada só no que está escrito (prazo mencionado, tom, tipo de pedido).
- "resumo": 1 frase curta e literal do que o e-mail pede ou informa. Não
  opine, não sugira solução — só resuma o conteúdo.
- "providencia_sugerida": 1 frase objetiva do que parece ser a próxima ação
  administrativa (ex.: "Encaminhar para setor de estágio", "Aguardar resposta
  do servidor"). Se não for possível saber, devolva "".
- "numero_processo" / "partes_mencionadas" / "datas_citadas_texto": copie
  literalmente o que está escrito no corpo (múltiplos itens separados por
  " | "). Nunca invente nem complete. Se não houver, devolva "".
- Se o eixo não tiver nenhum valor conhecido (marcado como "{CHAVE_SEM_VALOR}"),
  devolva um dicionário vazio {{}} para aquele eixo.
"""


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------

class ErroAnaliseSemantica(RuntimeError):
    """Falha de credencial, API ou resposta fora do formato esperado."""


class MotorSemanticoIA:
    """
    Encapsula cliente Groq + prompt + parsing. Uma instância é reaproveitada
    para o lote inteiro de e-mails (não recria cliente por linha).
    """

    def __init__(self, valores_por_eixo: Dict[str, List[str]], limiar_sugestao: float = LIMIAR_SUGESTAO_PADRAO):
        self.valores_por_eixo = valores_por_eixo
        self.limiar_sugestao = limiar_sugestao
        self._prompt_sistema = _prompt_sistema(valores_por_eixo)

    @staticmethod
    def _chave_groq() -> str:
        try:
            return str(st.secrets["groq"]["api_key"])
        except Exception as exc:
            raise ErroAnaliseSemantica(
                "Secret ausente: bloco [groq] com api_key não encontrado em st.secrets."
            ) from exc

    @staticmethod
    @st.cache_resource(show_spinner=False)
    def _cliente():
        from groq import Groq  # import local: só exigido quando este módulo é usado

        return Groq(api_key=MotorSemanticoIA._chave_groq())

    def analisar(self, id_mensagem: str, assunto: str, corpo: str) -> AnaliseSemanticaEmail:
        return self._analisar_cacheado(id_mensagem, assunto, corpo, self._prompt_sistema, self.limiar_sugestao)

    @staticmethod
    @st.cache_data(ttl=None, show_spinner=False)
    def _analisar_cacheado(
        id_mensagem: str, assunto: str, corpo: str, prompt_sistema: str, limiar_sugestao: float
    ) -> AnaliseSemanticaEmail:
        corpo_limitado = (corpo or "")[:6000]
        mensagem_usuario = f"ASSUNTO: {assunto}\n\nCORPO:\n{corpo_limitado}"

        try:
            resposta = MotorSemanticoIA._cliente().chat.completions.create(
                model=MODELO_GROQ,
                messages=[
                    {"role": "system", "content": prompt_sistema},
                    {"role": "user", "content": mensagem_usuario},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            bruto = resposta.choices[0].message.content
        except Exception as exc:
            return AnaliseSemanticaEmail(id_mensagem=id_mensagem, erro=f"Falha na API do Groq: {exc}")

        try:
            dados = json.loads(bruto)
        except (json.JSONDecodeError, TypeError):
            return AnaliseSemanticaEmail(id_mensagem=id_mensagem, erro=f"Resposta fora do formato JSON: {bruto!r}")

        graus_brutos = dados.get("graus") or {}
        graus: Dict[str, Dict[str, float]] = {}
        sugestao: Dict[str, Optional[str]] = {}
        grau_sugestao: Dict[str, float] = {}

        for eixo in EIXOS_FUZZY:
            graus_eixo = {}
            for valor, grau in (graus_brutos.get(eixo) or {}).items():
                try:
                    graus_eixo[str(valor)] = max(0.0, min(1.0, float(grau)))
                except (TypeError, ValueError):
                    continue
            graus[eixo] = graus_eixo
            if graus_eixo:
                melhor_valor, melhor_grau = max(graus_eixo.items(), key=lambda item: item[1])
                if melhor_grau >= limiar_sugestao:
                    sugestao[eixo] = melhor_valor
                    grau_sugestao[eixo] = melhor_grau
                else:
                    sugestao[eixo] = None
            else:
                sugestao[eixo] = None

        def _texto(valor) -> str:
            return "" if valor is None else str(valor).strip()

        urgencia = dados.get("urgencia")
        try:
            urgencia = max(0.0, min(1.0, float(urgencia)))
        except (TypeError, ValueError):
            urgencia = None

        return AnaliseSemanticaEmail(
            id_mensagem=id_mensagem,
            graus=graus,
            sugestao=sugestao,
            grau_sugestao=grau_sugestao,
            urgencia=urgencia,
            resumo=_texto(dados.get("resumo")),
            providencia_sugerida=_texto(dados.get("providencia_sugerida")),
            numero_processo=_texto(dados.get("numero_processo")),
            partes_mencionadas=_texto(dados.get("partes_mencionadas")),
            datas_citadas_texto=_texto(dados.get("datas_citadas_texto")),
        )


# ---------------------------------------------------------------------------
# Valores conhecidos por eixo — derivados dos dados já classificados,
# nunca de uma lista fixa no código (mesmo princípio de DE_PARA_LABELS).
# ---------------------------------------------------------------------------

def valores_observados_por_eixo(df: pd.DataFrame) -> Dict[str, List[str]]:
    resultado = {}
    for eixo in EIXOS_FUZZY:
        if eixo not in df.columns:
            resultado[eixo] = []
            continue
        vistos = df[eixo].fillna("").astype(str).str.strip()
        resultado[eixo] = sorted({v for v in vistos if v and v != "NAO_CLASSIFICADO"})
    return resultado


# ---------------------------------------------------------------------------
# Enriquecimento em lote
# ---------------------------------------------------------------------------

COLUNAS_IA = [
    "IA_URGENCIA", "IA_RESUMO", "IA_PROVIDENCIA_SUGERIDA",
    "IA_SUGESTAO_NATUREZA", "IA_SUGESTAO_TEMA", "IA_SUGESTAO_ESTADO_DEMANDA",
    "IA_ERRO",
]


def enriquecer_com_ia(
    df: pd.DataFrame,
    limiar_sugestao: float = LIMIAR_SUGESTAO_PADRAO,
    limite_chamadas: Optional[int] = None,
    somente_nao_classificados: bool = False,
) -> pd.DataFrame:
    """
    Roda a análise semântica em lote. Sem `limite_chamadas` (padrão None),
    processa a base inteira — cota liberada, sem necessidade de fatiar.
    `somente_nao_classificados=True` restringe às linhas em que ao menos um
    dos 3 eixos oficiais ainda está NAO_CLASSIFICADO (economiza chamada
    onde LABEL/REGRA já resolveram tudo, já que ali a IA só serviria pra
    extração de nº processo/partes/datas, cobertos à parte).
    """
    resultado = df.copy()
    for coluna in COLUNAS_IA + ["NUMERO_PROCESSO", "PARTES_MENCIONADAS", "DATAS_CITADAS_TEXTO",
                                  "ORIGEM_NUMERO_PROCESSO", "ORIGEM_PARTES", "ORIGEM_DATAS"]:
        if coluna not in resultado.columns:
            resultado[coluna] = ""

    valores_por_eixo = valores_observados_por_eixo(resultado)
    motor = MotorSemanticoIA(valores_por_eixo, limiar_sugestao)

    linhas_alvo = resultado
    if somente_nao_classificados:
        mascara = pd.Series(False, index=resultado.index)
        for eixo in EIXOS_FUZZY:
            if eixo in resultado.columns:
                mascara |= resultado[eixo].fillna("").astype(str).str.strip() == "NAO_CLASSIFICADO"
        linhas_alvo = resultado[mascara]

    chamadas_feitas = 0
    for indice, linha in linhas_alvo.iterrows():
        if limite_chamadas is not None and chamadas_feitas >= limite_chamadas:
            break

        id_mensagem = str(linha.get("ID_MENSAGEM", "")).strip()
        assunto = str(linha.get("ASSUNTO", "")).strip()
        corpo = str(linha.get("CORPO_EMAIL_TEXTO", "")).strip()
        if not id_mensagem or (not assunto and not corpo):
            continue

        analise = motor.analisar(id_mensagem, assunto, corpo)
        chamadas_feitas += 1

        if analise.erro:
            resultado.at[indice, "IA_ERRO"] = analise.erro
            continue

        resultado.at[indice, "IA_URGENCIA"] = analise.urgencia if analise.urgencia is not None else ""
        resultado.at[indice, "IA_RESUMO"] = analise.resumo
        resultado.at[indice, "IA_PROVIDENCIA_SUGERIDA"] = analise.providencia_sugerida
        resultado.at[indice, "IA_SUGESTAO_NATUREZA"] = analise.sugestao_para_card("NATUREZA")
        resultado.at[indice, "IA_SUGESTAO_TEMA"] = analise.sugestao_para_card("TEMA")
        resultado.at[indice, "IA_SUGESTAO_ESTADO_DEMANDA"] = analise.sugestao_para_card("ESTADO_DEMANDA")

        if not str(linha.get("NUMERO_PROCESSO", "")).strip() and analise.numero_processo:
            resultado.at[indice, "NUMERO_PROCESSO"] = analise.numero_processo
            resultado.at[indice, "ORIGEM_NUMERO_PROCESSO"] = "IA_GROQ"
        if not str(linha.get("PARTES_MENCIONADAS", "")).strip() and analise.partes_mencionadas:
            resultado.at[indice, "PARTES_MENCIONADAS"] = analise.partes_mencionadas
            resultado.at[indice, "ORIGEM_PARTES"] = "IA_GROQ"
        if not str(linha.get("DATAS_CITADAS_TEXTO", "")).strip() and analise.datas_citadas_texto:
            resultado.at[indice, "DATAS_CITADAS_TEXTO"] = analise.datas_citadas_texto
            resultado.at[indice, "ORIGEM_DATAS"] = "IA_GROQ"

    return resultado


def pendentes_de_extracao(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    checar = ["NUMERO_PROCESSO", "PARTES_MENCIONADAS", "DATAS_CITADAS_TEXTO"]
    vazio = pd.Series(False, index=df.index)
    for coluna in checar:
        if coluna in df.columns:
            vazio |= df[coluna].fillna("").astype(str).str.strip() == ""
    return int(vazio.sum())


# ---------------------------------------------------------------------------
# Cards — montagem pronta pra UI (dados oficiais + sugestão de IA separada)
# ---------------------------------------------------------------------------

def montar_card(linha: pd.Series) -> Dict[str, str]:
    """
    Um dicionário por e-mail, pronto pra renderizar em card no Streamlit.
    Tudo que vem de IA fica em campos com prefixo 'ia_' e rótulo próprio —
    nunca misturado com o dado oficial, pra equipe sempre distinguir visualmente.
    """
    def _v(chave: str) -> str:
        return str(linha.get(chave, "") or "").strip()

    return {
        "id_registro": _v("ID_REGISTRO"),
        "assunto": _v("ASSUNTO") or "(sem assunto)",
        "remetente": _v("REMETENTE_EMAIL"),
        "data_envio": _v("DATA_HORA_ENVIO"),
        "natureza_oficial": _v("NATUREZA") or "NAO_CLASSIFICADO",
        "tema_oficial": _v("TEMA") or "NAO_CLASSIFICADO",
        "estado_oficial": _v("ESTADO_DEMANDA") or "NAO_CLASSIFICADO",
        "situacao_prazo": _v("SITUACAO_PRAZO"),
        "numero_processo": _v("NUMERO_PROCESSO"),
        "origem_numero_processo": _v("ORIGEM_NUMERO_PROCESSO"),
        "status_providencia": _v("STATUS_PROVIDENCIA") or "Pendente",
        "ia_sugestao_natureza": _v("IA_SUGESTAO_NATUREZA"),
        "ia_sugestao_tema": _v("IA_SUGESTAO_TEMA"),
        "ia_sugestao_estado": _v("IA_SUGESTAO_ESTADO_DEMANDA"),
        "ia_urgencia": _v("IA_URGENCIA"),
        "ia_resumo": _v("IA_RESUMO"),
        "ia_providencia_sugerida": _v("IA_PROVIDENCIA_SUGERIDA"),
        "ia_erro": _v("IA_ERRO"),
    }


def filtrar_cards(
    df: pd.DataFrame,
    busca_livre: str = "",
    urgencia_minima: Optional[float] = None,
    apenas_com_sugestao_ia: bool = False,
) -> pd.DataFrame:
    """
    Filtros adicionais sobre a base já enriquecida. Combine com
    repositorio_painel.aplicar_filtros() para os filtros determinísticos
    (categoria, status, data) antes de chamar este.
    """
    resultado = df

    termo = (busca_livre or "").strip().lower()
    if termo:
        campos = ["ASSUNTO", "REMETENTE_EMAIL", "IA_RESUMO", "PARTES_MENCIONADAS", "NUMERO_PROCESSO"]
        alvo = pd.Series("", index=resultado.index)
        for campo in campos:
            if campo in resultado.columns:
                alvo = alvo + " " + resultado[campo].fillna("").astype(str).str.lower()
        resultado = resultado[alvo.str.contains(termo, na=False)]

    if urgencia_minima is not None and "IA_URGENCIA" in resultado.columns:
        urgencia_num = pd.to_numeric(resultado["IA_URGENCIA"], errors="coerce")
        resultado = resultado[urgencia_num.fillna(0) >= urgencia_minima]

    if apenas_com_sugestao_ia:
        colunas_sugestao = ["IA_SUGESTAO_NATUREZA", "IA_SUGESTAO_TEMA", "IA_SUGESTAO_ESTADO_DEMANDA"]
        presentes = [c for c in colunas_sugestao if c in resultado.columns]
        if presentes:
            tem_sugestao = pd.Series(False, index=resultado.index)
            for coluna in presentes:
                tem_sugestao |= resultado[coluna].fillna("").astype(str).str.strip() != ""
            resultado = resultado[tem_sugestao]

    return resultado
