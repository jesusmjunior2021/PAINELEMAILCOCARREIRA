"""
servico_sheets_painel.py — camada CRUA de acesso ao Google Sheets.

Responsabilidade única: abrir a planilha "PAINEL EMAIL COCARREIRA"
(aba PAINEL_GERAL) via Service Account, devolver os valores brutos e
gravar atualizações PONTUAIS de célula. Nenhuma regra de negócio aqui.

Mesmo padrão de servico_sheets.py do projeto BOLSATJMA.
"""

from __future__ import annotations

from typing import Any, Dict, List

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

ESCOPOS = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Ordem exata do cabeçalho gravado por cocarreira_painel_geral_captura.gs.
# NÃO alterar, NÃO reordenar. Mudança de schema é sempre aditiva (coluna
# nova entra no FIM, nunca substitui existente).
COLUNAS_PAINEL_GERAL: List[str] = [
    "ID_REGISTRO", "ID_MENSAGEM", "ID_THREAD",
    "DATA_HORA_ENVIO", "DATA_HORA_PROCESSAMENTO",
    "REMETENTE_NOME", "REMETENTE_EMAIL", "DESTINATARIOS", "ASSUNTO",
    "CORPO_EMAIL_TEXTO",
    "LABELS_GMAIL", "CATEGORIA_ASSUNTO", "STATUS_TRATAMENTO",
    "QTD_ANEXOS", "NOMES_ANEXOS", "LINKS_ANEXOS_DRIVE", "LINK_THREAD_GMAIL",
    "NOME_SERVIDOR", "MATRICULA_SERVIDOR",
    "DATA_TERMINO_CURSO", "PRAZO_LIMITE_ART25", "STATUS_PRAZO_ART25",
    "RISCO_NORMATIVO_ART17",
    "PROVIDENCIA_NECESSARIA", "STATUS_PROVIDENCIA", "OBSERVACOES",
    "LINK_PASTA_DRIVE",
]

# Únicas colunas que este app tem permissão de escrever.
COLUNAS_EDITAVEIS: List[str] = [
    "PROVIDENCIA_NECESSARIA", "STATUS_PROVIDENCIA", "OBSERVACOES",
]


class ErroAcessoPlanilha(RuntimeError):
    """Falha de credencial, compartilhamento ou aba inexistente."""


# ---------------------------------------------------------------------------
# Conexão
# ---------------------------------------------------------------------------

def _config() -> Dict[str, str]:
    try:
        bloco = st.secrets["planilha_painel_cocarreira"]
        return {
            "spreadsheet_id": bloco["spreadsheet_id"],
            "aba": bloco["aba"],
        }
    except Exception as exc:  # KeyError, StreamlitSecretNotFoundError...
        raise ErroAcessoPlanilha(
            "Secrets ausentes ou incompletos: bloco [planilha_painel_cocarreira] "
            "precisa conter spreadsheet_id e aba."
        ) from exc


@st.cache_resource(show_spinner=False)
def _cliente() -> gspread.Client:
    try:
        info = dict(st.secrets["gcp_service_account"])
    except Exception as exc:
        raise ErroAcessoPlanilha(
            "Secrets ausentes: bloco [gcp_service_account] não encontrado."
        ) from exc
    credenciais = Credentials.from_service_account_info(info, scopes=ESCOPOS)
    return gspread.authorize(credenciais)


def email_service_account() -> str:
    """Usado no diagnóstico: a planilha precisa estar compartilhada com este e-mail."""
    try:
        return str(st.secrets["gcp_service_account"]["client_email"])
    except Exception:
        return "(client_email não encontrado nos Secrets)"


def _aba():
    cfg = _config()
    try:
        planilha = _cliente().open_by_key(cfg["spreadsheet_id"])
    except gspread.exceptions.APIError as exc:
        raise ErroAcessoPlanilha(
            f"Não foi possível abrir a planilha {cfg['spreadsheet_id']}. "
            f"Confirme se ela está compartilhada com {email_service_account()}. "
            f"Detalhe: {exc}"
        ) from exc
    try:
        return planilha.worksheet(cfg["aba"])
    except gspread.exceptions.WorksheetNotFound as exc:
        raise ErroAcessoPlanilha(
            f"A aba '{cfg['aba']}' não existe na planilha."
        ) from exc


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def carregar_painel_geral() -> pd.DataFrame:
    """
    Lê a aba PAINEL_GERAL inteira e devolve um DataFrame de STRINGS,
    exatamente como está na fonte (sem conversão, sem preenchimento de
    lacuna). O cache de 300s existe para não repetir o incidente de
    rate limit (gspread APIError) já ocorrido no BOLSATJMA.
    """
    valores = _aba().get_all_values()
    if not valores:
        return pd.DataFrame(columns=COLUNAS_PAINEL_GERAL)

    cabecalho = [str(c).strip() for c in valores[0]]
    linhas = valores[1:]
    # Normaliza comprimento das linhas ao cabeçalho (Sheets corta trailing vazios).
    linhas = [linha + [""] * (len(cabecalho) - len(linha)) for linha in linhas]
    linhas = [linha[: len(cabecalho)] for linha in linhas]

    df = pd.DataFrame(linhas, columns=cabecalho)
    return df.astype(str)


def cabecalho_atual() -> List[str]:
    """Cabeçalho lido AO VIVO (sem cache) — usado antes de gravar, para
    localizar a coluna pela posição real e não por posição presumida."""
    return [str(c).strip() for c in _aba().row_values(1)]


# ---------------------------------------------------------------------------
# Escrita — sempre pontual, célula a célula, localizada por ID_REGISTRO
# ---------------------------------------------------------------------------

def atualizar_acompanhamento(atualizacoes: List[Dict[str, Any]]) -> int:
    """
    Grava apenas PROVIDENCIA_NECESSARIA, STATUS_PROVIDENCIA e OBSERVACOES.

    `atualizacoes` = lista de dicts contendo obrigatoriamente ID_REGISTRO
    e uma ou mais colunas editáveis. Nunca reescreve a aba inteira.
    Devolve a quantidade de células efetivamente enviadas.

    Levanta ErroAcessoPlanilha se um ID_REGISTRO não existir ou estiver
    duplicado — nesse caso NADA é gravado (nem parcialmente).
    """
    if not atualizacoes:
        return 0

    aba = _aba()
    cabecalho = [str(c).strip() for c in aba.row_values(1)]

    faltando = [c for c in ["ID_REGISTRO"] + COLUNAS_EDITAVEIS if c not in cabecalho]
    if faltando:
        raise ErroAcessoPlanilha(
            "A aba não contém as colunas esperadas: " + ", ".join(faltando)
        )

    col_id = cabecalho.index("ID_REGISTRO") + 1
    ids_coluna = aba.col_values(col_id)[1:]  # descarta o cabeçalho

    mapa_linhas: Dict[str, List[int]] = {}
    for deslocamento, valor in enumerate(ids_coluna):
        chave = str(valor).strip()
        if chave:
            mapa_linhas.setdefault(chave, []).append(deslocamento + 2)  # linha real

    lote = []
    for item in atualizacoes:
        chave = str(item.get("ID_REGISTRO", "")).strip()
        linhas = mapa_linhas.get(chave, [])
        if len(linhas) != 1:
            raise ErroAcessoPlanilha(
                f"ID_REGISTRO '{chave}' não foi localizado de forma única na planilha "
                f"({len(linhas)} ocorrência(s)). Nenhuma alteração foi gravada."
            )
        linha = linhas[0]
        for coluna in COLUNAS_EDITAVEIS:
            if coluna not in item:
                continue
            endereco = gspread.utils.rowcol_to_a1(linha, cabecalho.index(coluna) + 1)
            lote.append({"range": endereco, "values": [[str(item[coluna])]]})

    if not lote:
        return 0

    aba.batch_update(lote, value_input_option="USER_ENTERED")
    return len(lote)


# ---------------------------------------------------------------------------
# Abas de parâmetro (DE_PARA_LABELS, REGRAS_CLASSIFICACAO, PARAMETROS_PRAZO)
# ---------------------------------------------------------------------------

ABAS_PARAMETRO = ["DE_PARA_LABELS", "REGRAS_CLASSIFICACAO", "PARAMETROS_PRAZO"]


@st.cache_data(ttl=300, show_spinner=False)
def carregar_aba_parametro(nome_aba: str) -> pd.DataFrame:
    """
    Lê uma aba de parâmetro. Se ela ainda não existir na planilha, devolve
    DataFrame VAZIO em vez de quebrar — o app funciona sem classificação
    até que as abas sejam coladas, apenas sem os eixos novos.
    """
    cfg = _config()
    try:
        aba = _cliente().open_by_key(cfg["spreadsheet_id"]).worksheet(nome_aba)
    except gspread.exceptions.WorksheetNotFound:
        return pd.DataFrame()
    except gspread.exceptions.APIError as exc:
        raise ErroAcessoPlanilha(f"Falha ao ler a aba '{nome_aba}': {exc}") from exc

    valores = aba.get_all_values()
    if len(valores) < 2:
        return pd.DataFrame()
    cabecalho = [str(c).strip() for c in valores[0]]
    linhas = [linha + [""] * (len(cabecalho) - len(linha)) for linha in valores[1:]]
    linhas = [linha[: len(cabecalho)] for linha in linhas]
    df = pd.DataFrame(linhas, columns=cabecalho).astype(str)
    # A linha de exemplo distribuída no .xlsx é descartada automaticamente.
    if "OBSERVACAO" in df.columns:
        df = df[~df["OBSERVACAO"].str.contains("Linha de exemplo", case=False, na=False)]
    return df


def limpar_cache_parametros() -> None:
    carregar_aba_parametro.clear()
