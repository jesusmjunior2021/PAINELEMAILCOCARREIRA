"""
repositorio_painel.py — regra de negócio do Painel Geral COCARREIRA.

Toda decisão (filtro, KPI, classificação de prazo, cor) mora aqui.
O arquivo de UI não faz cálculo.

REGRA DURA DE NÃO-INVENÇÃO: lacuna ("Não apurável", célula vazia, data
ilegível) permanece lacuna. Nada é estimado, completado ou "consertado".
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

from servico_sheets_painel import COLUNAS_PAINEL_GERAL

# Valores reais produzidos pelo .gs — não é lista livre.
CATEGORIAS_CONHECIDAS: List[str] = [
    "AUXÍLIO BOLSA", "AG", "AVALIAÇÃO", "CONCURSOS", "CONTATOS", "CONVÊNIO",
    "COORDENADORIA", "CURSOS", "ESTÁGIO", "FISCAL DE CONTRATO",
    "HETEROIDENTIFICAÇÃO", "PROMOÇÃO/PUBLICIDADE", "SERVIDOR", "OUTROS",
]

STATUS_TRATAMENTO_CONHECIDOS: List[str] = [
    "Recebido", "Respondido", "Arquivado/Removido", "Encaminhado ao setor",
    "Enviado por nós", "Ocorrência Planus (sistema)", "Sem status definido",
]

# Único conjunto de valores que o app oferece para STATUS_PROVIDENCIA.
# "Pendente" é o default gravado pelo .gs; os demais são de controle
# interno da Coordenadoria (decisão de UI, não valor vindo da fonte).
STATUS_PROVIDENCIA_OPCOES: List[str] = [
    "Pendente", "Em andamento", "Concluída", "Sem providência necessária",
]

SEPARADOR_LISTA = " | "

# Códigos de prazo do art. 25 (mesma semântica de alerta_documentos.py)
VENCIDO = "VENCIDO"
VENCENDO = "VENCENDO"
DENTRO_DO_PRAZO = "DENTRO_DO_PRAZO"
NAO_APURAVEL = "NAO_APURAVEL"
SEM_CLASSIFICACAO = "SEM_CLASSIFICACAO"

ROTULO_PRAZO: Dict[str, str] = {
    VENCIDO: "Vencido",
    VENCENDO: "Vencendo (≤90 dias)",
    DENTRO_DO_PRAZO: "Dentro do prazo",
    NAO_APURAVEL: "Não apurável",
    SEM_CLASSIFICACAO: "Sem classificação na fonte",
}

# Paleta: decisão de UI deste app (não é dado institucional), seguindo a
# mesma convenção cromática de alerta_documentos.py no BOLSATJMA.
COR_PRAZO: Dict[str, str] = {
    VENCIDO: "#B3261E",            # vermelho
    VENCENDO: "#E8710A",           # laranja
    DENTRO_DO_PRAZO: "#1E7B34",    # verde
    NAO_APURAVEL: "#6B6B6B",       # cinza
    SEM_CLASSIFICACAO: "#6B6B6B",  # cinza
}

COLUNAS_TABELA_GERAL: List[str] = [
    "ID_REGISTRO", "DATA_HORA_ENVIO", "REMETENTE_EMAIL", "ASSUNTO",
    "NATUREZA", "TEMA", "ESTADO_DEMANDA", "SITUACAO_PRAZO",
    "NUMERO_PROCESSO", "STATUS_PROVIDENCIA",
]

COLUNAS_TABELA_BOLSA: List[str] = [
    "ID_REGISTRO", "DATA_HORA_ENVIO", "NOME_SERVIDOR", "MATRICULA_SERVIDOR",
    "ASSUNTO", "DATA_TERMINO_CURSO", "PRAZO_LIMITE_ART25", "STATUS_PRAZO_ART25",
    "STATUS_PROVIDENCIA",
]


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

def normalizar(df: pd.DataFrame) -> pd.DataFrame:
    """
    Garante a presença das colunas esperadas (as ausentes entram VAZIAS,
    nunca preenchidas), acrescenta colunas derivadas de apoio e mantém
    intactas eventuais colunas extras da planilha.
    """
    df = df.copy()

    for coluna in COLUNAS_PAINEL_GERAL:
        if coluna not in df.columns:
            df[coluna] = ""

    for coluna in df.columns:
        df[coluna] = df[coluna].fillna("").astype(str).str.strip()

    df["_DATA_ENVIO"] = _converter_datas(df["DATA_HORA_ENVIO"])
    df["_CODIGO_PRAZO"] = df["STATUS_PRAZO_ART25"].map(classificar_prazo)
    df["_QTD_ANEXOS_NUM"] = pd.to_numeric(df["QTD_ANEXOS"], errors="coerce").fillna(0).astype(int)
    return df


def _converter_datas(serie: pd.Series) -> pd.Series:
    """
    Converte DATA_HORA_ENVIO. O Sheets pode devolver o valor em formato
    brasileiro (dd/mm/aaaa) ou ISO. O que não for legível vira NaT e
    permanece explicitamente "sem data" — nunca é chutado.
    """
    texto = serie.fillna("").astype(str).str.strip()
    eh_iso = texto.str.match(r"^\d{4}-\d{2}-\d{2}")

    # ISO (aaaa-mm-dd...) NÃO pode ser lido com dayfirst — viraria dia/mês trocados.
    iso = pd.to_datetime(
        texto.where(eh_iso), errors="coerce", format="mixed"
    )
    brasileiro = pd.to_datetime(
        texto.where(~eh_iso), errors="coerce", dayfirst=True, format="mixed"
    )
    return iso.fillna(brasileiro)


def classificar_prazo(texto_status: str) -> str:
    """
    Traduz o texto gravado pelo .gs em código de cor/agrupamento.
    Textos possíveis na fonte:
      "Vencido há N dia(s)" / "Vence em N dia(s)" /
      "Dentro do prazo (N dias restantes)" / "Não apurável" / ""
    """
    texto = (texto_status or "").strip()
    if not texto:
        return SEM_CLASSIFICACAO
    normalizado = texto.lower()
    if normalizado.startswith("vencido"):
        return VENCIDO
    if normalizado.startswith("vence em"):
        return VENCENDO
    if normalizado.startswith("dentro do prazo"):
        return DENTRO_DO_PRAZO
    if "não apurável" in normalizado or "nao apuravel" in normalizado:
        return NAO_APURAVEL
    return SEM_CLASSIFICACAO


def dias_do_status(texto_status: str) -> Optional[int]:
    """Extrai o número de dias já calculado pelo .gs, se houver. Não recalcula."""
    encontrado = re.search(r"(\d+)", texto_status or "")
    return int(encontrado.group(1)) if encontrado else None


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------

def aplicar_filtros(
    df: pd.DataFrame,
    categorias: Optional[List[str]] = None,
    status_tratamento: Optional[List[str]] = None,
    intervalo_datas: Optional[Tuple[pd.Timestamp, pd.Timestamp]] = None,
    incluir_sem_data: bool = True,
    busca_livre: str = "",
    apenas_pendentes: bool = False,
) -> pd.DataFrame:
    """Busca livre atua em ASSUNTO e REMETENTE_EMAIL (case-insensitive)."""
    resultado = df

    if categorias:
        resultado = resultado[resultado["CATEGORIA_ASSUNTO"].isin(categorias)]

    if status_tratamento:
        resultado = resultado[resultado["STATUS_TRATAMENTO"].isin(status_tratamento)]

    if intervalo_datas:
        inicio, fim = intervalo_datas
        dentro = resultado["_DATA_ENVIO"].between(inicio, fim)
        if incluir_sem_data:
            dentro = dentro | resultado["_DATA_ENVIO"].isna()
        resultado = resultado[dentro]

    termo = (busca_livre or "").strip()
    if termo:
        alvo = (
            resultado["ASSUNTO"].str.lower()
            + " "
            + resultado["REMETENTE_EMAIL"].str.lower()
        )
        resultado = resultado[alvo.str.contains(re.escape(termo.lower()), na=False)]

    if apenas_pendentes:
        resultado = resultado[resultado["STATUS_PROVIDENCIA"].str.casefold() == "pendente"]

    return resultado


def somente_auxilio_bolsa(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["CATEGORIA_ASSUNTO"] == "AUXÍLIO BOLSA"]


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

def kpis_gerais(df: pd.DataFrame) -> Dict[str, int]:
    return {
        "total_emails": int(len(df)),
        "categorias_distintas": int(df["CATEGORIA_ASSUNTO"].replace("", pd.NA).nunique(dropna=True)),
        "providencias_pendentes": int((df["STATUS_PROVIDENCIA"].str.casefold() == "pendente").sum()),
        "com_anexo": int((df["_QTD_ANEXOS_NUM"] > 0).sum()),
        "auxilio_bolsa": int((df["CATEGORIA_ASSUNTO"] == "AUXÍLIO BOLSA").sum()),
    }


def contagem_por(df: pd.DataFrame, coluna: str) -> pd.DataFrame:
    contagem = (
        df[coluna]
        .replace("", "(vazio na fonte)")
        .value_counts()
        .rename_axis(coluna)
        .reset_index(name="QUANTIDADE")
    )
    return contagem


def contagem_prazo_bolsa(df_bolsa: pd.DataFrame) -> Dict[str, int]:
    contagens = {codigo: 0 for codigo in ROTULO_PRAZO}
    for codigo, quantidade in df_bolsa["_CODIGO_PRAZO"].value_counts().items():
        contagens[codigo] = int(quantidade)
    return contagens


# ---------------------------------------------------------------------------
# Apoio à exibição
# ---------------------------------------------------------------------------

def separar_lista(valor: str) -> List[str]:
    """NOMES_ANEXOS e LINKS_ANEXOS_DRIVE vêm concatenados por ' | '."""
    if not valor:
        return []
    return [parte.strip() for parte in valor.split(SEPARADOR_LISTA.strip()) if parte.strip()]


def parear_anexos(nomes: str, links: str) -> List[Tuple[str, str]]:
    """
    Pareia nome↔link por posição. Se as quantidades divergirem, o que
    faltar é exibido como vazio — não se inventa correspondência.
    """
    lista_nomes = separar_lista(nomes)
    lista_links = separar_lista(links)
    tamanho = max(len(lista_nomes), len(lista_links))
    return [
        (
            lista_nomes[i] if i < len(lista_nomes) else "(nome ausente na fonte)",
            lista_links[i] if i < len(lista_links) else "",
        )
        for i in range(tamanho)
    ]


def rotulo_registro(linha: pd.Series) -> str:
    data = linha.get("DATA_HORA_ENVIO", "") or "(sem data)"
    assunto = linha.get("ASSUNTO", "") or "(sem assunto)"
    categoria = linha.get("CATEGORIA_ASSUNTO", "") or "(sem categoria)"
    return f"#{linha.get('ID_REGISTRO', '?')} · {data} · [{categoria}] {assunto}"


def limites_de_data(df: pd.DataFrame) -> Optional[Tuple[pd.Timestamp, pd.Timestamp]]:
    validas = df["_DATA_ENVIO"].dropna()
    if validas.empty:
        return None
    return validas.min(), validas.max()


# ---------------------------------------------------------------------------
# Orquestração: normalizar -> classificar -> calcular prazo
# ---------------------------------------------------------------------------

import classificador as _cls          # noqa: E402
import motor_prazos as _prazos        # noqa: E402

COLUNAS_TABELA_DEMANDA: List[str] = [
    "ASSUNTO", "TEMA", "NATUREZA", "ESTADO_DEMANDA", "QTD_MENSAGENS",
    "ULTIMA_MENSAGEM", "RESPONDIDA_POR_NOS", "NUMERO_PROCESSO",
    "SITUACAO_PRAZO", "PRAZO_LIMITE",
]


def montar_visao(
    df_bruto: pd.DataFrame,
    df_de_para: Optional[pd.DataFrame] = None,
    df_regras: Optional[pd.DataFrame] = None,
    df_prazos: Optional[pd.DataFrame] = None,
    feriados: Optional[List[str]] = None,
) -> pd.DataFrame:
    """Pipeline único usado por todas as páginas do app."""
    df = normalizar(df_bruto)
    df = _cls.classificar(df, df_de_para, df_regras)
    df = _prazos.calcular(df, df_prazos, feriados=feriados)
    return df


def sem_ruido(df: pd.DataFrame) -> pd.DataFrame:
    """Só o que é trabalho da Coordenadoria."""
    if "E_RUIDO" not in df.columns:
        return df
    return df[df["E_RUIDO"] != "SIM"]


def apenas_ruido(df: pd.DataFrame) -> pd.DataFrame:
    if "E_RUIDO" not in df.columns:
        return df.iloc[0:0]
    return df[df["E_RUIDO"] == "SIM"]


def filtrar_por_eixo(df: pd.DataFrame, coluna: str, valores: Optional[List[str]]) -> pd.DataFrame:
    if not valores or coluna not in df.columns:
        return df
    return df[df[coluna].isin(valores)]


# ---------------------------------------------------------------------------
# Visão por DEMANDA (thread) — a unidade de gestão de prazo
# ---------------------------------------------------------------------------

CAIXA_INSTITUCIONAL = "cocarreira@tjma.jus.br"

# Ordem de gravidade: a demanda herda a pior situação entre suas mensagens.
_GRAVIDADE = {
    _prazos.VENCIDO: 0,
    _prazos.VENCENDO: 1,
    _prazos.DENTRO_DO_PRAZO: 2,
    _prazos.SEM_DATA_BASE: 3,
    _prazos.SEM_PRAZO_PARAMETRIZADO: 4,
}


def agregar_por_demanda(df: pd.DataFrame) -> pd.DataFrame:
    """
    Uma linha por ID_THREAD. Necessário porque o painel grava uma linha por
    MENSAGEM: nos dados reais, 109 mensagens correspondem a 75 demandas, e
    uma única thread ('Redefinição de senha - CNJ') responde por 20 linhas.
    Contar mensagem como demanda distorce qualquer indicador de carga.
    """
    if df.empty:
        return df.iloc[0:0].assign(QTD_MENSAGENS=[], ULTIMA_MENSAGEM=[], RESPONDIDA_POR_NOS=[])

    registros = []
    for id_thread, grupo in df.groupby("ID_THREAD", sort=False):
        grupo = grupo.sort_values("_DATA_ENVIO", na_position="first")
        primeira = grupo.iloc[0]
        situacao = min(
            grupo["SITUACAO_PRAZO"],
            key=lambda s: _GRAVIDADE.get(s, 9),
        ) if "SITUACAO_PRAZO" in grupo.columns else ""
        linha_situacao = grupo[grupo["SITUACAO_PRAZO"] == situacao].iloc[0] if situacao else primeira
        ultima = grupo["_DATA_ENVIO"].max()
        registros.append(
            {
                "ID_THREAD": id_thread,
                "ID_REGISTRO": primeira["ID_REGISTRO"],
                "ASSUNTO": _primeiro_nao_vazio(grupo["ASSUNTO"]),
                "REMETENTE_EMAIL": primeira["REMETENTE_EMAIL"],
                "NATUREZA": _moda(grupo["NATUREZA"]),
                "TEMA": _moda(grupo["TEMA"]),
                "ESTADO_DEMANDA": _moda(grupo["ESTADO_DEMANDA"]),
                "NUMERO_PROCESSO": _primeiro_nao_vazio(grupo["NUMERO_PROCESSO"]),
                "QTD_MENSAGENS": int(len(grupo)),
                "ULTIMA_MENSAGEM": "" if pd.isna(ultima) else ultima.strftime("%d/%m/%Y %H:%M"),
                "RESPONDIDA_POR_NOS": "SIM" if (grupo["REMETENTE_EMAIL"] == CAIXA_INSTITUCIONAL).any() else "NÃO",
                "SITUACAO_PRAZO": situacao,
                "PRAZO_LIMITE": linha_situacao.get("PRAZO_LIMITE", ""),
                "LINK_THREAD_GMAIL": _primeiro_nao_vazio(grupo["LINK_THREAD_GMAIL"]),
                "_DATA_ENVIO": ultima,
            }
        )
    return pd.DataFrame(registros)


def _primeiro_nao_vazio(serie: pd.Series) -> str:
    for valor in serie:
        if str(valor).strip():
            return str(valor).strip()
    return ""


def _moda(serie: pd.Series) -> str:
    validos = [v for v in serie if str(v).strip() and v != _cls.NAO_CLASSIFICADO]
    if not validos:
        return _cls.NAO_CLASSIFICADO
    return pd.Series(validos).value_counts().idxmax()


def kpis_demanda(df_demandas: pd.DataFrame) -> Dict[str, int]:
    if df_demandas.empty:
        return {"demandas": 0, "sem_resposta": 0, "vencidas": 0, "vencendo": 0}
    return {
        "demandas": int(len(df_demandas)),
        "sem_resposta": int((df_demandas["RESPONDIDA_POR_NOS"] == "NÃO").sum()),
        "vencidas": int((df_demandas["SITUACAO_PRAZO"] == _prazos.VENCIDO).sum()),
        "vencendo": int((df_demandas["SITUACAO_PRAZO"] == _prazos.VENCENDO).sum()),
    }
