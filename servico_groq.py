"""
servico_groq.py — extração assistida por IA (Groq), complementar ao
classificador.py determinístico.

PRINCÍPIO (mesmo do resto do projeto): a IA NUNCA classifica NATUREZA,
TEMA ou ESTADO_DEMANDA — isso continua vindo só de LABEL/REGRA, como já é.
Este módulo só entra para um problema diferente: procurar, DENTRO DO TEXTO
LIVRE do corpo do e-mail, três coisas que o regex de classificador.py às
vezes não pega porque o remetente escreveu por extenso, sem a palavra-âncora
esperada, ou de forma solta no meio do parágrafo:

    - NUMERO_PROCESSO       (nº de processo/requisição/protocolo)
    - PARTES_MENCIONADAS    (nomes de servidores/partes citados no corpo)
    - DATAS_CITADAS_TEXTO   (datas/prazos mencionados em texto livre)

REGRA DURA, igual ao resto do projeto: a IA só roda nas linhas em que o
campo determinístico está VAZIO. Se `classificador.py` já achou o número
via regex, o Groq nem é chamado para aquele campo naquela linha — o
determinístico sempre tem prioridade e nunca é sobrescrito.

Toda vez que um campo vem da IA, a origem fica registrada explicitamente
em ORIGEM_NUMERO_PROCESSO / ORIGEM_PARTES / ORIGEM_DATAS = "IA_GROQ",
nunca misturada com REGRA_APLICADA. Se o próprio modelo não achar nada no
texto, o campo continua vazio — a IA tem instrução explícita de não
inventar nem inferir, só transcrever o que está escrito.

CUSTO/CHAMADAS: a extração é sob demanda (chamar enriquecer_com_ia()
explicitamente), não automática dentro de montar_visao(), e com cache por
ID_MENSAGEM — para não repetir chamada no mesmo e-mail a cada reload da
página nem estourar limite de taxa da API.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

MODELO_GROQ = "llama-3.3-70b-versatile"

CAMPOS_IA = ["NUMERO_PROCESSO", "PARTES_MENCIONADAS", "DATAS_CITADAS_TEXTO"]

PROMPT_SISTEMA = """Você é um extrator de texto, não um classificador e não um assistente
de redação. Sua única tarefa é ler o e-mail abaixo e transcrever, literalmente,
o que já está escrito nele — nunca deduzir, completar ou supor.

Responda SOMENTE com um JSON válido, exatamente neste formato, sem nenhum
texto antes ou depois:

{
  "numero_processo": "",
  "partes_mencionadas": "",
  "datas_citadas_texto": ""
}

Regras:
- "numero_processo": copie o número de processo, requisição, protocolo ou
  chamado citado no corpo, se houver. Se houver mais de um, separe por " | ".
  Se não houver NENHUM número desse tipo escrito no texto, devolva "".
- "partes_mencionadas": copie nomes de pessoas (servidores, partes, terceiros)
  citados no corpo do e-mail como envolvidos no assunto, separados por " | ".
  NÃO inclua o nome do remetente nem de quem está apenas assinando o e-mail.
  Se não houver nome de pessoa envolvida citado no corpo, devolva "".
- "datas_citadas_texto": copie datas ou prazos mencionados no corpo do texto
  (ex.: "até 15/03", "prazo de 30 dias", "vencimento em dezembro"), separados
  por " | ". Se não houver nenhuma data/prazo mencionado no corpo, devolva "".
- Nunca invente. Nunca complete um número ou nome parcial. Se estiver
  incompleto ou ilegível no texto, devolva exatamente como está, mesmo que
  pareça incompleto.
"""


class ErroExtracaoIA(RuntimeError):
    """Falha de credencial, API ou resposta fora do formato esperado."""


def _chave_groq() -> str:
    try:
        return str(st.secrets["groq"]["api_key"])
    except Exception as exc:
        raise ErroExtracaoIA(
            "Secret ausente: bloco [groq] com api_key não encontrado em st.secrets."
        ) from exc


@st.cache_resource(show_spinner=False)
def _cliente():
    from groq import Groq  # import local: só é exigido se este módulo for usado

    return Groq(api_key=_chave_groq())


def _texto(valor) -> str:
    return "" if valor is None else str(valor).strip()


@st.cache_data(ttl=None, show_spinner=False)
def _extrair_uma_mensagem(id_mensagem: str, assunto: str, corpo: str) -> Dict[str, str]:
    """
    Chama o Groq para UM e-mail. Cacheada por ID_MENSAGEM: o mesmo e-mail
    nunca gera duas chamadas de API, mesmo em reloads futuros da página.
    """
    corpo_limitado = corpo[:6000]  # teto de contexto — corpo maior que isso é truncado, não descartado
    mensagem_usuario = f"ASSUNTO: {assunto}\n\nCORPO:\n{corpo_limitado}"

    try:
        resposta = _cliente().chat.completions.create(
            model=MODELO_GROQ,
            messages=[
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": mensagem_usuario},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        bruto = resposta.choices[0].message.content
    except Exception as exc:
        raise ErroExtracaoIA(f"Falha ao chamar a API do Groq para {id_mensagem}: {exc}") from exc

    try:
        dados = json.loads(bruto)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ErroExtracaoIA(
            f"Resposta do Groq para {id_mensagem} não é JSON válido: {bruto!r}"
        ) from exc

    return {
        "NUMERO_PROCESSO": _texto(dados.get("numero_processo")),
        "PARTES_MENCIONADAS": _texto(dados.get("partes_mencionadas")),
        "DATAS_CITADAS_TEXTO": _texto(dados.get("datas_citadas_texto")),
    }


def enriquecer_com_ia(
    df: pd.DataFrame,
    limite_chamadas: int = 30,
) -> pd.DataFrame:
    """
    Preenche NUMERO_PROCESSO / PARTES_MENCIONADAS / DATAS_CITADAS_TEXTO
    usando o Groq, SÓ nas linhas em que o campo determinístico está vazio.

    `limite_chamadas`: teto de chamadas de API nesta execução (proteção
    contra estourar limite de taxa ou custo ao rodar numa base grande de
    uma vez só). Linhas além do limite ficam como estavam — rode de novo
    para continuar completando nas próximas linhas.

    Erros de API em UMA linha não derrubam as demais: ficam registrados
    em ERRO_EXTRACAO_IA para aquela linha específica, e o processamento
    continua para as próximas.
    """
    resultado = df.copy()

    for coluna in CAMPOS_IA:
        if coluna not in resultado.columns:
            resultado[coluna] = ""
    for coluna in ["ORIGEM_NUMERO_PROCESSO", "ORIGEM_PARTES", "ORIGEM_DATAS", "ERRO_EXTRACAO_IA"]:
        if coluna not in resultado.columns:
            resultado[coluna] = ""

    chamadas_feitas = 0

    for indice, linha in resultado.iterrows():
        if chamadas_feitas >= limite_chamadas:
            break

        precisa_numero = not _texto(linha.get("NUMERO_PROCESSO"))
        precisa_partes = not _texto(linha.get("PARTES_MENCIONADAS"))
        precisa_datas = not _texto(linha.get("DATAS_CITADAS_TEXTO"))

        if not (precisa_numero or precisa_partes or precisa_datas):
            continue  # já tem tudo de fonte determinística — não gasta chamada

        id_mensagem = _texto(linha.get("ID_MENSAGEM"))
        assunto = _texto(linha.get("ASSUNTO"))
        corpo = _texto(linha.get("CORPO_EMAIL_TEXTO"))
        if not corpo and not assunto:
            continue

        try:
            extraido = _extrair_uma_mensagem(id_mensagem, assunto, corpo)
        except ErroExtracaoIA as exc:
            resultado.at[indice, "ERRO_EXTRACAO_IA"] = str(exc)
            chamadas_feitas += 1
            continue

        chamadas_feitas += 1

        if precisa_numero and extraido["NUMERO_PROCESSO"]:
            resultado.at[indice, "NUMERO_PROCESSO"] = extraido["NUMERO_PROCESSO"]
            resultado.at[indice, "ORIGEM_NUMERO_PROCESSO"] = "IA_GROQ"
        if precisa_partes and extraido["PARTES_MENCIONADAS"]:
            resultado.at[indice, "PARTES_MENCIONADAS"] = extraido["PARTES_MENCIONADAS"]
            resultado.at[indice, "ORIGEM_PARTES"] = "IA_GROQ"
        if precisa_datas and extraido["DATAS_CITADAS_TEXTO"]:
            resultado.at[indice, "DATAS_CITADAS_TEXTO"] = extraido["DATAS_CITADAS_TEXTO"]
            resultado.at[indice, "ORIGEM_DATAS"] = "IA_GROQ"

    return resultado


def pendentes_de_extracao(df: pd.DataFrame) -> int:
    """Quantas linhas ainda têm pelo menos um dos 3 campos vazio — para mostrar na UI
    quantas chamadas faltam antes de disparar enriquecer_com_ia()."""
    if df.empty:
        return 0
    vazio_numero = df.get("NUMERO_PROCESSO", pd.Series(dtype=str)).fillna("").astype(str).str.strip() == ""
    vazio_partes = df.get("PARTES_MENCIONADAS", pd.Series(dtype=str)).fillna("").astype(str).str.strip() == ""
    vazio_datas = df.get("DATAS_CITADAS_TEXTO", pd.Series(dtype=str)).fillna("").astype(str).str.strip() == ""
    return int((vazio_numero | vazio_partes | vazio_datas).sum())
