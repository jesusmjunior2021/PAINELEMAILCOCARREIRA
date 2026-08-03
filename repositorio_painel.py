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
# MAT-COCARREIRA-EMAILAUTO-002 — verificação de demanda, atribuição de
# servidor responsável e prazo manual de solução.
# ---------------------------------------------------------------------------

# Tri-estado pedido pela Coordenadoria: verificada / pendente / resolvida.
# Independente de STATUS_PROVIDENCIA (que já existe) — este eixo é sobre
# CONFERÊNCIA do e-mail, não sobre o andamento da providência em si.
ESTADO_VERIFICACAO_OPCOES: List[str] = ["Pendente", "Verificada", "Resolvida"]

COR_ESTADO_VERIFICACAO: Dict[str, str] = {
    "Pendente": "#C9A227",
    "Verificada": "#1565C0",
    "Resolvida": "#1E7B34",
    "": "#6B6B6B",
}

# Estados que OBRIGAM observação de acompanhamento preenchida (o que foi
# feito), por pedido explícito da Coordenadoria — não é mero rótulo solto.
ESTADOS_QUE_EXIGEM_OBSERVACAO = {"Verificada", "Resolvida"}

# Colunas que o usuário de fato digita/seleciona no editor. As demais colunas
# editáveis (matrícula, data de atribuição, data-limite do prazo manual) são
# DERIVADAS por aplicar_atribuicao() e ficam desabilitadas na grade — nunca
# digitadas à mão, conforme pedido ("ao invés de ter que digitar").
COLUNAS_EDITAVEIS_DIRETAMENTE: List[str] = [
    "PROVIDENCIA_NECESSARIA", "STATUS_PROVIDENCIA", "OBSERVACOES",
    "ESTADO_VERIFICACAO", "OBSERVACAO_VERIFICACAO",
    "SERVIDOR_RESPONSAVEL_NOME", "PRAZO_MANUAL_DIAS",
]

# Prazos oferecidos para atribuição manual (dias corridos), a critério de
# quem atribui a tarefa — não é prazo normativo automático (esse já existe
# em PARAMETROS_PRAZO/motor_prazos.py). "" = sem prazo manual definido.
PRAZO_MANUAL_DIAS_OPCOES: List[str] = ["", "5", "10", "15", "20", "30", "45", "60", "90"]

# Semiótica de cores replicando os marcadores/labels reais do Gmail da
# cocarreira@tjma.jus.br (ver imagens anexadas e cocarreira_diagnostico_labels.gs).
# Decisão de UI deste app — não é dado institucional/normativo.
COR_CATEGORIA: Dict[str, str] = {
    "AUXÍLIO BOLSA": "#8E24AA",
    "COORDENADORIA": "#009688",
    "HETEROIDENTIFICAÇÃO": "#D93025",
    "PROMOÇÃO/PUBLICIDADE": "#F4B183",
    "AG": "#6B6B6B",
    "AVALIAÇÃO": "#6B6B6B",
    "CONCURSOS": "#6B6B6B",
    "CONTATOS": "#6B6B6B",
    "CONVÊNIO": "#6B6B6B",
    "CURSOS": "#6B6B6B",
    "ESTÁGIO": "#6B6B6B",
    "FISCAL DE CONTRATO": "#6B6B6B",
    "SERVIDOR": "#6B6B6B",
    "OUTROS": "#9E9E9E",
}

COR_STATUS_TRATAMENTO: Dict[str, str] = {
    "Respondido": "#1E7B34",
    "Encaminhado ao setor": "#E8710A",
    "Enviado por nós": "#1565C0",
    "Recebido": "#6B6B6B",
    "Arquivado/Removido": "#6B6B6B",
    "Ocorrência Planus (sistema)": "#6B6B6B",
    "Sem status definido": "#9E9E9E",
}


def badge_html(texto: str, cor: str) -> str:
    """Selo colorido inline — mesma semiótica de cor usada no Gmail da caixa."""
    if not texto:
        return ""
    return (
        f"<span style='background:{cor};color:#FFFFFF;padding:2px 10px;"
        f"border-radius:10px;font-size:0.78rem;font-weight:600;"
        f"white-space:nowrap;'>{texto}</span>"
    )


def preparar_servidores(df: Optional[pd.DataFrame]) -> Dict[str, str]:
    """
    Lê a aba SERVIDORES_COORDENADORIA e devolve {NOME: MATRICULA} só para
    ATIVO=SIM. Fonte da lista suspensa — nunca digitação livre de nome de
    servidor no editor de acompanhamento.
    """
    mapa: Dict[str, str] = {}
    if df is None or df.empty:
        return mapa
    for _, linha in df.iterrows():
        if str(linha.get("ATIVO", "")).strip().upper() not in ("SIM", "S", "TRUE", "1"):
            continue
        nome = str(linha.get("NOME", "")).strip()
        if nome:
            mapa[nome] = str(linha.get("MATRICULA", "")).strip()
    return mapa


def classificar_prazo_manual(data_limite_texto: str, hoje: Optional[pd.Timestamp] = None) -> str:
    """Mesmo código de cor de classificar_prazo(), aplicado à data-limite
    manual (PRAZO_MANUAL_DATA_LIMITE), calculada por aplicar_atribuicao()."""
    texto = (data_limite_texto or "").strip()
    if not texto:
        return SEM_CLASSIFICACAO
    limite = pd.to_datetime(texto, errors="coerce", dayfirst=True)
    if pd.isna(limite):
        return SEM_CLASSIFICACAO
    referencia = pd.Timestamp(hoje or pd.Timestamp.now()).normalize()
    dias = int((limite.normalize() - referencia).days)
    if dias < 0:
        return VENCIDO
    if dias <= 5:
        return VENCENDO
    return DENTRO_DO_PRAZO


def aplicar_atribuicao(
    alteracoes: List[Dict[str, str]],
    df_original: pd.DataFrame,
    mapa_servidores: Dict[str, str],
    hoje: Optional[pd.Timestamp] = None,
) -> List[Dict[str, str]]:
    """
    Preenche por DERIVAÇÃO — nunca por digitação — os campos que dependem de
    outro campo escolhido pelo usuário:

      SERVIDOR_RESPONSAVEL_NOME (selecionado)
        -> SERVIDOR_RESPONSAVEL_MATRICULA (buscada em SERVIDORES_COORDENADORIA)
        -> DATA_ATRIBUICAO_TAREFA (hoje, só na PRIMEIRA atribuição; se a linha
           já tinha atribuição anterior, a data não é sobrescrita)

      PRAZO_MANUAL_DIAS (selecionado)
        -> PRAZO_MANUAL_DATA_LIMITE (data-base + dias corridos; data-base é a
           DATA_ATRIBUICAO_TAREFA vigente, ou hoje quando não houver)

    Não altera nenhuma outra coluna. `alteracoes` é modificada in-place e
    também retornada, para uso direto em atualizar_acompanhamento().
    """
    referencia = pd.Timestamp(hoje or pd.Timestamp.now()).normalize()
    indice_original = df_original.set_index("ID_REGISTRO") if not df_original.empty else df_original

    for mudanca in alteracoes:
        id_registro = str(mudanca.get("ID_REGISTRO", ""))
        linha_original = (
            indice_original.loc[id_registro]
            if id_registro and not df_original.empty and id_registro in indice_original.index
            else None
        )

        if "SERVIDOR_RESPONSAVEL_NOME" in mudanca:
            nome = str(mudanca["SERVIDOR_RESPONSAVEL_NOME"]).strip()
            mudanca["SERVIDOR_RESPONSAVEL_MATRICULA"] = mapa_servidores.get(nome, "") if nome else ""
            data_existente = (
                str(linha_original.get("DATA_ATRIBUICAO_TAREFA", "")).strip()
                if linha_original is not None else ""
            )
            if nome and not data_existente:
                mudanca["DATA_ATRIBUICAO_TAREFA"] = referencia.strftime("%d/%m/%Y")
            elif not nome:
                mudanca["DATA_ATRIBUICAO_TAREFA"] = ""

        if "PRAZO_MANUAL_DIAS" in mudanca:
            dias_texto = str(mudanca["PRAZO_MANUAL_DIAS"]).strip()
            if dias_texto:
                data_atribuicao_atual = mudanca.get("DATA_ATRIBUICAO_TAREFA")
                if data_atribuicao_atual is None and linha_original is not None:
                    data_atribuicao_atual = str(linha_original.get("DATA_ATRIBUICAO_TAREFA", "")).strip()
                base = pd.to_datetime(data_atribuicao_atual, errors="coerce", dayfirst=True) if data_atribuicao_atual else None
                if base is None or pd.isna(base):
                    base = referencia
                try:
                    limite = base.normalize() + pd.Timedelta(days=int(dias_texto))
                    mudanca["PRAZO_MANUAL_DATA_LIMITE"] = limite.strftime("%d/%m/%Y")
                except (ValueError, TypeError):
                    mudanca["PRAZO_MANUAL_DATA_LIMITE"] = ""
            else:
                mudanca["PRAZO_MANUAL_DATA_LIMITE"] = ""

    return alteracoes


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

# ---------------------------------------------------------------------------
# Alerta semiótico único — a resposta direta para "isso é lixo, urgente,
# precisa de atenção ou já está em dia?" numa cor só por registro.
# Combina E_RUIDO (classificador.py) + SITUACAO_PRAZO (motor_prazos.py) +
# STATUS_PROVIDENCIA/STATUS_TRATAMENTO — nada aqui é recalculado, só
# reaproveita o que os dois módulos já produziram, numa prioridade única.
# ---------------------------------------------------------------------------

LIXO_RUIDO = "LIXO_RUIDO"
VENCIDO_ALERTA = "VENCIDO"
VENCENDO_ALERTA = "VENCENDO"
ATENCAO_SEM_PRAZO = "ATENCAO_SEM_PRAZO"
EM_DIA = "EM_DIA"

ROTULO_ALERTA: Dict[str, str] = {
    LIXO_RUIDO: "Lixo / ruído — sem providência",
    VENCIDO_ALERTA: "Vencido — providência urgente",
    VENCENDO_ALERTA: "Vencendo — atenção",
    ATENCAO_SEM_PRAZO: "Sem prazo formal, mas sem resposta",
    EM_DIA: "Em dia",
}

ICONE_ALERTA: Dict[str, str] = {
    LIXO_RUIDO: "⚪",
    VENCIDO_ALERTA: "🔴",
    VENCENDO_ALERTA: "🟠",
    ATENCAO_SEM_PRAZO: "🟡",
    EM_DIA: "🟢",
}

COR_ALERTA: Dict[str, str] = {
    LIXO_RUIDO: "#6B6B6B",
    VENCIDO_ALERTA: "#B3261E",
    VENCENDO_ALERTA: "#E8710A",
    ATENCAO_SEM_PRAZO: "#C9A227",
    EM_DIA: "#1E7B34",
}

_STATUS_TRATAMENTO_RESOLVIDO = {"Respondido", "Arquivado/Removido", "Enviado por nós"}
_STATUS_PROVIDENCIA_RESOLVIDO = {"Concluída", "Sem providência necessária"}


def _alerta_linha(linha: pd.Series) -> str:
    if str(linha.get("E_RUIDO", "")) == "SIM":
        return LIXO_RUIDO

    situacao = str(linha.get("SITUACAO_PRAZO", ""))
    if situacao == _prazos.VENCIDO:
        return VENCIDO_ALERTA
    if situacao == _prazos.VENCENDO:
        return VENCENDO_ALERTA
    if situacao == _prazos.DENTRO_DO_PRAZO:
        return EM_DIA

    # Sem prazo parametrizado ou sem data-base: só é "em dia" se já foi
    # tratado; senão é a categoria "precisa de decisão humana, sem prazo formal".
    if (
        str(linha.get("STATUS_TRATAMENTO", "")) in _STATUS_TRATAMENTO_RESOLVIDO
        or str(linha.get("STATUS_PROVIDENCIA", "")) in _STATUS_PROVIDENCIA_RESOLVIDO
    ):
        return EM_DIA
    return ATENCAO_SEM_PRAZO


def calcular_alerta(df: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta a coluna ALERTA (um dos 5 códigos acima) ao DataFrame."""
    resultado = df.copy()
    if resultado.empty:
        resultado["ALERTA"] = pd.Series(dtype=str)
        return resultado
    resultado["ALERTA"] = resultado.apply(_alerta_linha, axis=1)
    return resultado


def resumo_alerta(df: pd.DataFrame) -> Dict[str, int]:
    contagens = {codigo: 0 for codigo in ROTULO_ALERTA}
    if "ALERTA" not in df.columns:
        return contagens
    for codigo, quantidade in df["ALERTA"].value_counts().items():
        if codigo in contagens:
            contagens[codigo] = int(quantidade)
    return contagens


ORDEM_GRAVIDADE_ALERTA = [VENCIDO_ALERTA, VENCENDO_ALERTA, ATENCAO_SEM_PRAZO, EM_DIA, LIXO_RUIDO]


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


# ---------------------------------------------------------------------------
# FILTRO POR LABEL DO GMAIL — os marcadores reais, como fonte de recorte
# ---------------------------------------------------------------------------
# TEMA é o vocabulário normalizado; LABEL é o marcador literal que a equipe
# aplicou no Gmail. Os dois não são a mesma coisa: quatro labels de turma caem
# num TEMA só, e um e-mail pode ter vários labels ao mesmo tempo. Para
# conferência e compliance, filtrar pelo marcador literal é o que permite
# responder "me mostre o que está etiquetado como CURSOS/Turma 13 e 14".

SEM_LABEL = "(sem marcador)"


def labels_distintos(df: pd.DataFrame) -> List[str]:
    """Todos os labels presentes, já desmembrados do campo concatenado."""
    if df.empty or "LABELS_GMAIL" not in df.columns:
        return []
    encontrados = set()
    for valor in df["LABELS_GMAIL"]:
        partes = separar_lista(valor)
        if partes:
            encontrados.update(partes)
    return sorted(encontrados)


def filtrar_por_label(
    df: pd.DataFrame,
    labels: Optional[List[str]],
    exigir_todos: bool = False,
) -> pd.DataFrame:
    """
    Recorta pelos marcadores escolhidos.
      exigir_todos=False -> e-mail com QUALQUER um dos labels (união)
      exigir_todos=True  -> e-mail com TODOS eles (interseção)
    O valor especial SEM_LABEL seleciona quem não tem marcador nenhum — é o
    recorte que mostra o tamanho do trabalho de etiquetagem ainda pendente.
    """
    if not labels or "LABELS_GMAIL" not in df.columns:
        return df

    quer_sem_label = SEM_LABEL in labels
    alvos = [l for l in labels if l != SEM_LABEL]

    def casa(valor: str) -> bool:
        presentes = separar_lista(valor)
        if quer_sem_label and not presentes:
            return True
        if not alvos:
            return False
        if exigir_todos:
            return all(a in presentes for a in alvos)
        return any(a in presentes for a in alvos)

    return df[df["LABELS_GMAIL"].map(casa)]


def contagem_por_label(df: pd.DataFrame) -> pd.DataFrame:
    """
    Mensagens e conversas por marcador. A soma das linhas pode ultrapassar o
    total: um e-mail com três labels é contado nos três. Isso é proposital e
    está avisado na UI — não é erro de contagem.
    """
    if df.empty:
        return pd.DataFrame(columns=["LABEL", "MENSAGENS", "CONVERSAS", "TEMA_PREDOMINANTE"])

    linhas = []
    for label in labels_distintos(df):
        recorte = filtrar_por_label(df, [label])
        if recorte.empty:
            continue
        linhas.append(
            {
                "LABEL": label,
                "MENSAGENS": int(len(recorte)),
                "CONVERSAS": int(recorte["ID_THREAD"].nunique()),
                "TEMA_PREDOMINANTE": _moda(recorte["TEMA"]) if "TEMA" in recorte.columns else "",
            }
        )

    sem = df[df["LABELS_GMAIL"].map(lambda v: not separar_lista(v))]
    if not sem.empty:
        linhas.append(
            {
                "LABEL": SEM_LABEL,
                "MENSAGENS": int(len(sem)),
                "CONVERSAS": int(sem["ID_THREAD"].nunique()),
                "TEMA_PREDOMINANTE": _moda(sem["TEMA"]) if "TEMA" in sem.columns else "",
            }
        )

    return pd.DataFrame(linhas).sort_values("MENSAGENS", ascending=False)


# ---------------------------------------------------------------------------
# COMPLIANCE — checagens auditáveis, cada uma com a lista dos registros
# ---------------------------------------------------------------------------
# Cada checagem é uma pergunta objetiva com resposta verificável na planilha.
# Nenhuma delas usa IA, estimativa ou julgamento: ou o dado está lá, ou não.

GRAVE = "GRAVE"
ATENCAO = "ATENÇÃO"
INFORMATIVO = "INFORMATIVO"

COR_GRAVIDADE_COMPLIANCE = {
    GRAVE: "#B3261E",
    ATENCAO: "#E8710A",
    INFORMATIVO: "#5A7D9A",
}


def _mask_id_duplicado(df: pd.DataFrame) -> pd.Series:
    if "ID_MENSAGEM" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["ID_MENSAGEM"].duplicated(keep=False) & (df["ID_MENSAGEM"].str.strip() != "")


def _mask_anexo_sem_link(df: pd.DataFrame) -> pd.Series:
    if "_QTD_ANEXOS_NUM" not in df.columns:
        return pd.Series(False, index=df.index)
    return (df["_QTD_ANEXOS_NUM"] > 0) & (df["LINKS_ANEXOS_DRIVE"].str.strip() == "")


def _mask_bolsa_sem_servidor(df: pd.DataFrame) -> pd.Series:
    if "TEMA" not in df.columns:
        return pd.Series(False, index=df.index)
    return (df["TEMA"] == "AUXILIO_BOLSA") & (df["NOME_SERVIDOR"].str.strip() == "")


CHECAGENS_COMPLIANCE = [
    {
        "chave": "ID_DUPLICADO",
        "gravidade": GRAVE,
        "titulo": "ID_MENSAGEM duplicado",
        "descricao": "A mesma mensagem foi gravada mais de uma vez. Quebra a "
                     "idempotência da captura e infla toda contagem.",
        "mask": _mask_id_duplicado,
    },
    {
        "chave": "SEM_ID",
        "gravidade": GRAVE,
        "titulo": "Linha sem ID_MENSAGEM",
        "descricao": "Registro sem chave. Não pode ser atualizado nem auditado, "
                     "e o commit de propostas do Gem o rejeita.",
        "mask": lambda df: df["ID_MENSAGEM"].str.strip() == "",
    },
    {
        "chave": "ANEXO_SEM_LINK",
        "gravidade": GRAVE,
        "titulo": "Tem anexo mas não tem link do Drive",
        "descricao": "O e-mail declara anexo e o arquivo não foi salvo. Documento "
                     "de processo pode estar só no Gmail, sem cópia no acervo.",
        "mask": _mask_anexo_sem_link,
    },
    {
        "chave": "SEM_DATA",
        "gravidade": ATENCAO,
        "titulo": "Sem DATA_HORA_ENVIO legível",
        "descricao": "Sem data não há prazo, não entra em série temporal e some "
                     "de qualquer filtro por período.",
        "mask": lambda df: df["_DATA_ENVIO"].isna(),
    },
    {
        "chave": "SEM_MARCADOR",
        "gravidade": ATENCAO,
        "titulo": "Sem marcador no Gmail",
        "descricao": "Classificado só por regra automática. A conferência humana "
                     "pelo marcador ainda não aconteceu.",
        "mask": lambda df: df["LABELS_GMAIL"].str.strip() == "",
    },
    {
        "chave": "TEMA_ABERTO",
        "gravidade": ATENCAO,
        "titulo": "TEMA não classificado",
        "descricao": "Nem label nem regra resolveram. É a fila de trabalho do Gem.",
        "mask": lambda df: df["TEMA"] == "NAO_CLASSIFICADO",
    },
    {
        "chave": "BOLSA_SEM_SERVIDOR",
        "gravidade": ATENCAO,
        "titulo": "Auxílio Bolsa sem servidor identificado",
        "descricao": "Sem servidor não há data de término, e sem ela o prazo do "
                     "art. 25 fica 'Não apurável'. Conferir SERVIDORES_MONITORADOS.",
        "mask": _mask_bolsa_sem_servidor,
    },
    {
        "chave": "PROVIDENCIA_PENDENTE",
        "gravidade": INFORMATIVO,
        "titulo": "Providência pendente",
        "descricao": "Estado inicial de todo registro. Alto por si só não é "
                     "problema; alto com prazo vencido é.",
        "mask": lambda df: df["STATUS_PROVIDENCIA"].str.casefold() == "pendente",
    },
]


def checagens_compliance(df: pd.DataFrame) -> pd.DataFrame:
    """Resumo: uma linha por checagem, com contagem e percentual."""
    total = max(len(df), 1)
    linhas = []
    for checagem in CHECAGENS_COMPLIANCE:
        try:
            quantidade = int(checagem["mask"](df).sum())
        except (KeyError, AttributeError):
            continue  # coluna ausente: a checagem simplesmente não se aplica
        linhas.append(
            {
                "GRAVIDADE": checagem["gravidade"],
                "CHECAGEM": checagem["titulo"],
                "QTD": quantidade,
                "% DO TOTAL": round(100 * quantidade / total, 1),
                "O QUE SIGNIFICA": checagem["descricao"],
                "_chave": checagem["chave"],
            }
        )
    return pd.DataFrame(linhas)


def registros_da_checagem(df: pd.DataFrame, chave: str) -> pd.DataFrame:
    for checagem in CHECAGENS_COMPLIANCE:
        if checagem["chave"] == chave:
            try:
                return df[checagem["mask"](df)]
            except (KeyError, AttributeError):
                return df.iloc[0:0]
    return df.iloc[0:0]


def indice_conformidade(df: pd.DataFrame) -> Dict[str, float]:
    """
    Três percentuais que resumem a saúde do acervo. Não é nota, é medida:
      integridade  — sem duplicata, sem chave faltando, anexo com link
      rastreio     — proporção classificada com origem declarada
      etiquetagem  — proporção conferida por marcador humano no Gmail
    """
    total = max(len(df), 1)
    graves = 0
    for checagem in CHECAGENS_COMPLIANCE:
        if checagem["gravidade"] != GRAVE:
            continue
        try:
            graves += int(checagem["mask"](df).sum())
        except (KeyError, AttributeError):
            pass

    com_origem = 0
    if "ORIGEM_CLASSIFICACAO" in df.columns:
        com_origem = int((df["ORIGEM_CLASSIFICACAO"] != "NENHUMA").sum())

    com_label = 0
    if "LABELS_GMAIL" in df.columns:
        com_label = int((df["LABELS_GMAIL"].str.strip() != "").sum())

    return {
        "integridade": round(100 * max(0, total - graves) / total, 1),
        "rastreio": round(100 * com_origem / total, 1),
        "etiquetagem": round(100 * com_label / total, 1),
    }
