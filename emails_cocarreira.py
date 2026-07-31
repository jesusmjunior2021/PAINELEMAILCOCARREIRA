# -*- coding: utf-8 -*-
"""
emails_cocarreira.py — Camada de REGRA DE NEGÓCIO + UI para a aba
EMAILS_COCARREIRA (populada pelo Apps Script MAT-COCARREIRA-EMAILAUTO-002).

Objetivo (pedido do Adm. Jesus): refletir no app os e-mails já capturados
pelo GS (325+ linhas), com os links de anexo/Drive clicáveis direto na
linha, filtros, e a possibilidade de marcar cada e-mail como
RESOLVIDO/NÃO RESOLVIDO e VERIFICADO/NÃO VERIFICADO.

Arquitetura — mesmo padrão de distribuicao_processos.py:
  - servico_sheets.py continua sendo a ÚNICA camada que fala com a API
    (aqui só é ampliada com `garantir_colunas_controle`, aditiva).
  - repositorio_bolsistas.py fornece o CRUD genérico (upsert_por_id) —
    reaproveitado aqui com coluna_chave="ID_EMAIL".
  - Este arquivo só acrescenta o que é específico do domínio "e-mails
    cocarreira": schema de controle, filtros e renderização.

Regra dura preservada: nunca apaga nem reordena o que o Apps Script já
gravou (ID_EMAIL, THREAD_ID, DATA_EMAIL, REMETENTE_*, ASSUNTO,
CATEGORIA_DETECTADA, MATRICULA_MATCH, NOME_MATCH, PROCESSO_MATCH,
QTD_ANEXOS, IDS_ANEXOS, LINKS_DRIVE_ANEXOS, LINK_DRIVE_NO_CORPO,
PASTA_DRIVE_URL, STATUS_MATCH, CHECK_DOCUMENTACAO,
DATA_PROCESSAMENTO_GS) — só ACRESCENTA duas colunas de controle ao final.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import servico_sheets as sheets
import repositorio_bolsistas as repo

NOME_ABA = "EMAILS_COCARREIRA"
CAMPO_ID = "ID_EMAIL"

# Colunas de controle que este módulo é responsável por garantir que
# existam na aba — sempre ao final, nunca sobrescrevendo o que o GS grava.
COLUNAS_CONTROLE = {
    "STATUS_RESOLUCAO": "NAO_RESOLVIDO",  # RESOLVIDO / NAO_RESOLVIDO
    "VERIFICADO": "NAO",                   # SIM / NAO
}

OPCOES_STATUS_RESOLUCAO = ["NAO_RESOLVIDO", "RESOLVIDO"]
OPCOES_VERIFICADO = ["NAO", "SIM"]

# Colunas de link — viram coluna clicável na tabela (LinkColumn), não
# texto cru. Nomes exatamente como o GS grava.
COLUNAS_LINK = ["LINKS_DRIVE_ANEXOS", "LINK_DRIVE_NO_CORPO", "PASTA_DRIVE_URL"]


def garantir_estrutura() -> list[str]:
    """Roda a migração aditiva (cria as colunas de controle se ainda não
    existirem). Idempotente — chamar de novo não duplica nem reseta valor
    já marcado por alguém. Devolve as colunas criadas agora (vazia se já
    existiam)."""
    return sheets.garantir_colunas_controle(NOME_ABA, COLUNAS_CONTROLE)


@st.cache_data(ttl=120, show_spinner="Lendo e-mails da cocarreira...")
def _carregar_emails() -> pd.DataFrame:
    return repo.carregar_aba(NOME_ABA, linha_cabecalho=1, header_overrides={})


def _aplicar_filtros(df: pd.DataFrame) -> pd.DataFrame:
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        categorias = ["Todas"] + sorted(
            [c for c in df.get("CATEGORIA_DETECTADA", pd.Series(dtype=str)).unique() if str(c).strip()]
        )
        categoria_sel = st.selectbox("Categoria", categorias, key="ec_filtro_categoria")

    with col2:
        opcoes_check = ["Todos"] + sorted(
            [c for c in df.get("CHECK_DOCUMENTACAO", pd.Series(dtype=str)).unique() if str(c).strip()]
        )
        check_sel = st.selectbox("Documentação", opcoes_check, key="ec_filtro_check")

    with col3:
        resolucao_sel = st.selectbox("Resolução", ["Todos"] + OPCOES_STATUS_RESOLUCAO, key="ec_filtro_resolucao")

    with col4:
        verificado_sel = st.selectbox("Verificado", ["Todos"] + OPCOES_VERIFICADO, key="ec_filtro_verificado")

    texto_livre = st.text_input(
        "Buscar por nome, matrícula, assunto ou processo", key="ec_filtro_texto"
    )

    filtrado = df.copy()
    if categoria_sel != "Todas" and "CATEGORIA_DETECTADA" in filtrado.columns:
        filtrado = filtrado[filtrado["CATEGORIA_DETECTADA"] == categoria_sel]
    if check_sel != "Todos" and "CHECK_DOCUMENTACAO" in filtrado.columns:
        filtrado = filtrado[filtrado["CHECK_DOCUMENTACAO"] == check_sel]
    if resolucao_sel != "Todos" and "STATUS_RESOLUCAO" in filtrado.columns:
        filtrado = filtrado[filtrado["STATUS_RESOLUCAO"] == resolucao_sel]
    if verificado_sel != "Todos" and "VERIFICADO" in filtrado.columns:
        filtrado = filtrado[filtrado["VERIFICADO"] == verificado_sel]
    if texto_livre.strip():
        termo = texto_livre.strip().upper()
        colunas_busca = [c for c in ["NOME_MATCH", "MATRICULA_MATCH", "ASSUNTO", "PROCESSO_MATCH", "REMETENTE_NOME"]
                         if c in filtrado.columns]
        mascara = pd.Series(False, index=filtrado.index)
        for coluna in colunas_busca:
            mascara |= filtrado[coluna].astype(str).str.upper().str.contains(termo, na=False)
        filtrado = filtrado[mascara]

    return filtrado


def _config_colunas_link() -> dict:
    config = {}
    for coluna in COLUNAS_LINK:
        config[coluna] = st.column_config.LinkColumn(coluna, display_text="Abrir ↗")
    config["STATUS_RESOLUCAO"] = st.column_config.SelectboxColumn(
        "STATUS_RESOLUCAO", options=OPCOES_STATUS_RESOLUCAO, required=True,
    )
    config["VERIFICADO"] = st.column_config.SelectboxColumn(
        "VERIFICADO", options=OPCOES_VERIFICADO, required=True,
    )
    return config


def renderizar_emails_cocarreira(abas: dict) -> None:
    st.subheader("📧 E-mails Cocarreira — Auxílio Bolsa")
    st.caption(
        "Fonte: aba EMAILS_COCARREIRA (Apps Script MAT-COCARREIRA-EMAILAUTO-002). "
        "Prioritária sobre EMAILBOLSA — aba mais nova e mais completa."
    )

    if st.button("🔄 Garantir colunas de controle (uma vez)", key="ec_garantir_estrutura"):
        criadas = garantir_estrutura()
        if criadas:
            st.success(f"Coluna(s) criada(s): {', '.join(criadas)}.")
        else:
            st.info("Colunas de controle já existiam — nada a fazer.")
        _carregar_emails.clear()

    df = _carregar_emails()
    if df.empty:
        st.warning(
            "Nenhum dado lido da aba EMAILS_COCARREIRA ainda. Se o GS já rodou e tem "
            "linhas na planilha, clique em 'Garantir colunas de controle' acima e recarregue."
        )
        return

    total = len(df)
    resolvidos = int((df.get("STATUS_RESOLUCAO") == "RESOLVIDO").sum()) if "STATUS_RESOLUCAO" in df.columns else 0
    verificados = int((df.get("VERIFICADO") == "SIM").sum()) if "VERIFICADO" in df.columns else 0
    sem_documentacao = int((df.get("CHECK_DOCUMENTACAO") == "NAO_ENVIADO").sum()) if "CHECK_DOCUMENTACAO" in df.columns else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total de e-mails", total)
    c2.metric("Resolvidos", resolvidos)
    c3.metric("Verificados", verificados)
    c4.metric("Sem documentação", sem_documentacao)

    df_filtrado = _aplicar_filtros(df)
    st.caption(f"{len(df_filtrado)} de {total} e-mail(s) após filtro.")

    colunas_exibir = [c for c in df_filtrado.columns if c not in ("_LINHA_REAL", "CORPO_EMAIL")]
    df_editor = df_filtrado[colunas_exibir].copy()

    df_editado = st.data_editor(
        df_editor,
        key="ec_editor",
        column_config=_config_colunas_link(),
        disabled=[c for c in colunas_exibir if c not in ("STATUS_RESOLUCAO", "VERIFICADO")],
        hide_index=True,
        use_container_width=True,
    )

    if st.button("💾 Salvar alterações de Resolução/Verificação", key="ec_salvar", type="primary"):
        atualizacoes = []
        for idx in df_editado.index:
            linha_original = df_filtrado.loc[idx]
            linha_editada = df_editado.loc[idx]
            mudou = (
                str(linha_original.get("STATUS_RESOLUCAO", "")) != str(linha_editada.get("STATUS_RESOLUCAO", ""))
                or str(linha_original.get("VERIFICADO", "")) != str(linha_editada.get("VERIFICADO", ""))
            )
            if not mudou:
                continue
            numero_linha_real = int(df.loc[idx, "_LINHA_REAL"])
            colunas_reais = [c for c in df.columns if c != "_LINHA_REAL"]
            valores = []
            for coluna in colunas_reais:
                if coluna in ("STATUS_RESOLUCAO", "VERIFICADO"):
                    valores.append(str(linha_editada.get(coluna, "")))
                else:
                    valores.append(str(df.loc[idx, coluna]))
            atualizacoes.append((numero_linha_real, valores))

        if not atualizacoes:
            st.info("Nada mudou desde a última leitura.")
        else:
            gravadas = sheets.atualizar_linhas_em_lote(NOME_ABA, atualizacoes)
            st.success(f"{gravadas} linha(s) atualizada(s) na planilha.")
            _carregar_emails.clear()
            st.rerun()
