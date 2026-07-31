"""
app_painel_cocarreira.py — UI Streamlit do Painel Geral de E-mails da
Coordenadoria de Acompanhamento e Desenvolvimento na Carreira (COCARREIRA /
COGEX-MA / TJMA).

Lê a aba PAINEL_GERAL da planilha "PAINEL EMAIL COCARREIRA", alimentada pelo
Apps Script cocarreira_painel_geral_captura.gs. Este app NÃO lê o Gmail,
NÃO classifica com IA e NÃO gera rascunho de resposta.

Camadas:
  servico_sheets_painel.py  -> gspread cru
  repositorio_painel.py     -> regra de negócio
  app_painel_cocarreira.py  -> só UI (este arquivo)

Execução:  streamlit run app_painel_cocarreira.py
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

import classificador as cls
import motor_prazos as prazos
import repositorio_painel as repo
from servico_sheets_painel import (
    ABAS_PARAMETRO,
    COLUNAS_EDITAVEIS,
    ErroAcessoPlanilha,
    atualizar_acompanhamento,
    carregar_aba_parametro,
    carregar_painel_geral,
    email_service_account,
    limpar_cache_parametros,
)

st.set_page_config(
    page_title="Painel Geral de E-mails — COCARREIRA/COGEX",
    page_icon="📬",
    layout="wide",
)

# Paleta institucional sóbria — decisão de UI deste app, não dado normativo.
AZUL_INSTITUCIONAL = "#0B3C5D"


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------

def carregar_parametros() -> dict:
    return {nome: carregar_aba_parametro(nome) for nome in ABAS_PARAMETRO}


def carregar(parametros: dict) -> pd.DataFrame:
    """PAINEL_GERAL + classificação + prazos, num pipeline só."""
    return repo.montar_visao(
        carregar_painel_geral(),
        df_de_para=parametros.get("DE_PARA_LABELS"),
        df_regras=parametros.get("REGRAS_CLASSIFICACAO"),
        df_prazos=parametros.get("PARAMETROS_PRAZO"),
    )


def cartao_prazo(codigo: str, quantidade: int) -> str:
    cor = repo.COR_PRAZO[codigo]
    rotulo = repo.ROTULO_PRAZO[codigo]
    return (
        f"<div style='border-left:6px solid {cor};background:rgba(0,0,0,0.03);"
        f"padding:12px 16px;border-radius:6px;'>"
        f"<div style='font-size:0.8rem;color:{cor};font-weight:600;"
        f"text-transform:uppercase;letter-spacing:.04em;'>{rotulo}</div>"
        f"<div style='font-size:2rem;font-weight:700;line-height:1.1;'>{quantidade}</div>"
        f"</div>"
    )


# ---------------------------------------------------------------------------
# Detalhe de um registro
# ---------------------------------------------------------------------------

def bloco_detalhe(linha: pd.Series) -> None:
    coluna_esq, coluna_dir = st.columns([3, 2])

    with coluna_esq:
        st.markdown(f"**Assunto:** {linha['ASSUNTO'] or '_(vazio na fonte)_'}")
        st.markdown(
            f"**Remetente:** {linha['REMETENTE_NOME'] or '—'} "
            f"(`{linha['REMETENTE_EMAIL'] or '—'}`)"
        )
        st.markdown(f"**Destinatários:** {linha['DESTINATARIOS'] or '—'}")
        st.markdown(f"**Enviado em:** {linha['DATA_HORA_ENVIO'] or '—'}")
        st.markdown(f"**Labels no Gmail:** {linha['LABELS_GMAIL'] or '—'}")

    with coluna_dir:
        st.markdown(f"**Categoria:** {linha['CATEGORIA_ASSUNTO'] or '—'}")
        st.markdown(f"**Status de tratamento:** {linha['STATUS_TRATAMENTO'] or '—'}")
        st.markdown(f"**Status da providência:** {linha['STATUS_PROVIDENCIA'] or '—'}")
        if linha["LINK_THREAD_GMAIL"]:
            st.link_button("Abrir no Gmail", linha["LINK_THREAD_GMAIL"])
        if linha.get("LINK_PASTA_DRIVE"):
            st.link_button("📁 Abrir pasta no Drive (anexos)", linha["LINK_PASTA_DRIVE"])

    if linha["CATEGORIA_ASSUNTO"] == "AUXÍLIO BOLSA":
        st.divider()
        st.markdown("##### Atenção especial — Auxílio Bolsa")
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**Servidor:** {linha['NOME_SERVIDOR'] or 'Não identificado'}")
        c2.markdown(f"**Matrícula:** {linha['MATRICULA_SERVIDOR'] or '—'}")
        c3.markdown(f"**Término do curso:** {linha['DATA_TERMINO_CURSO'] or '—'}")
        c1.markdown(f"**Prazo-limite (art. 25):** {linha['PRAZO_LIMITE_ART25'] or '—'}")
        codigo = repo.classificar_prazo(linha["STATUS_PRAZO_ART25"])
        c2.markdown(
            f"**Situação do prazo:** "
            f"<span style='color:{repo.COR_PRAZO[codigo]};font-weight:600;'>"
            f"{linha['STATUS_PRAZO_ART25'] or 'Sem classificação na fonte'}</span>",
            unsafe_allow_html=True,
        )
        if linha["RISCO_NORMATIVO_ART17"]:
            # Texto exibido NA ÍNTEGRA, sem resumo e sem escolher dispositivo.
            st.warning(linha["RISCO_NORMATIVO_ART17"])

    anexos = repo.parear_anexos(linha["NOMES_ANEXOS"], linha["LINKS_ANEXOS_DRIVE"])
    if anexos:
        st.divider()
        st.markdown(f"##### Anexos ({len(anexos)})")
        for nome, link in anexos:
            if link:
                st.markdown(f"- [{nome}]({link})")
            else:
                st.markdown(f"- {nome} _(link ausente na fonte)_")

    st.divider()
    st.markdown("##### Corpo do e-mail (texto integral, como capturado)")
    if linha["CORPO_EMAIL_TEXTO"]:
        st.text_area(
            "corpo",
            value=linha["CORPO_EMAIL_TEXTO"],
            height=320,
            disabled=True,
            label_visibility="collapsed",
            key=f"corpo_{linha['ID_REGISTRO']}_{linha['ID_MENSAGEM']}",
        )
    else:
        st.caption("Corpo vazio na fonte.")


def pagina_alertas(df: pd.DataFrame) -> None:
    st.subheader("🚦 Painel de Alertas — triagem em uma tela só")
    st.caption(
        "Combina ruído (classificador.py) + prazo (motor_prazos.py) + status de "
        "providência numa cor só por e-mail. Não recalcula nada — só prioriza o "
        "que os dois módulos já classificaram."
    )

    df_alerta = repo.calcular_alerta(df)
    resumo = repo.resumo_alerta(df_alerta)

    colunas = st.columns(5)
    for coluna, codigo in zip(colunas, repo.ORDEM_GRAVIDADE_ALERTA):
        cor = repo.COR_ALERTA[codigo]
        coluna.markdown(
            f"<div style='border-left:6px solid {cor};background:rgba(0,0,0,0.03);"
            f"padding:12px 16px;border-radius:6px;'>"
            f"<div style='font-size:0.72rem;color:{cor};font-weight:600;"
            f"text-transform:uppercase;'>{repo.ICONE_ALERTA[codigo]} {repo.ROTULO_ALERTA[codigo]}</div>"
            f"<div style='font-size:1.9rem;font-weight:700;line-height:1.1;'>"
            f"{resumo[codigo]}</div></div>",
            unsafe_allow_html=True,
        )

    if df_alerta.empty:
        st.info("Nenhum registro nos filtros atuais.")
        return

    st.divider()
    prioridade = st.multiselect(
        "Mostrar somente",
        [f"{repo.ICONE_ALERTA[c]} {repo.ROTULO_ALERTA[c]}" for c in repo.ORDEM_GRAVIDADE_ALERTA],
        default=[
            f"{repo.ICONE_ALERTA[c]} {repo.ROTULO_ALERTA[c]}"
            for c in [repo.VENCIDO_ALERTA, repo.VENCENDO_ALERTA, repo.ATENCAO_SEM_PRAZO]
        ],
    )
    codigos_selecionados = [
        c for c in repo.ORDEM_GRAVIDADE_ALERTA
        if f"{repo.ICONE_ALERTA[c]} {repo.ROTULO_ALERTA[c]}" in prioridade
    ] or repo.ORDEM_GRAVIDADE_ALERTA

    recorte = df_alerta[df_alerta["ALERTA"].isin(codigos_selecionados)].copy()
    recorte["_ORDEM"] = recorte["ALERTA"].map({c: i for i, c in enumerate(repo.ORDEM_GRAVIDADE_ALERTA)})
    recorte = recorte.sort_values(["_ORDEM", "DIAS_RESTANTES"], na_position="last")

    if recorte.empty:
        st.info("Nenhum registro no recorte de alerta escolhido.")
        return

    st.markdown(f"**{len(recorte)} registro(s)** — do mais urgente pro menos urgente.")
    for _, linha in recorte.head(200).iterrows():
        codigo = linha["ALERTA"]
        cor = repo.COR_ALERTA[codigo]
        icone = repo.ICONE_ALERTA[codigo]
        prazo_txt = f" · {linha['SITUACAO_PRAZO']} ({linha['DIAS_RESTANTES']}d)" if pd.notna(linha.get("DIAS_RESTANTES")) else ""
        with st.expander(f"{icone} {repo.rotulo_registro(linha)}{prazo_txt}"):
            st.markdown(
                f"<span style='color:{cor};font-weight:600;'>{repo.ROTULO_ALERTA[codigo]}</span>",
                unsafe_allow_html=True,
            )
            bloco_detalhe(linha)


# ---------------------------------------------------------------------------
# Página 1 — Dashboard geral
# ---------------------------------------------------------------------------

def pagina_dashboard(df: pd.DataFrame, df_completo: pd.DataFrame) -> None:
    st.subheader("Visão geral")

    kpis = repo.kpis_gerais(df)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("E-mails (filtro atual)", kpis["total_emails"])
    c2.metric("Conversas (threads)", int(df["ID_THREAD"].nunique()) if not df.empty else 0)
    c3.metric("Providências pendentes", kpis["providencias_pendentes"])
    c4.metric("Com anexo", kpis["com_anexo"])
    c5.metric("Auxílio Bolsa", kpis["auxilio_bolsa"])

    if df.empty:
        st.info("Nenhum registro corresponde aos filtros aplicados.")
        return

    st.divider()
    grafico_esq, grafico_dir = st.columns(2)

    with grafico_esq:
        st.markdown("**Distribuição por TEMA**")
        dados = repo.contagem_por(df, "TEMA")
        grafico = (
            alt.Chart(dados)
            .mark_bar(color=AZUL_INSTITUCIONAL)
            .encode(
                x=alt.X("QUANTIDADE:Q", title="E-mails"),
                y=alt.Y("TEMA:N", sort="-x", title=None),
                tooltip=["TEMA", "QUANTIDADE"],
            )
            .properties(height=max(220, 26 * len(dados)))
        )
        st.altair_chart(grafico, use_container_width=True)

    with grafico_dir:
        st.markdown("**Distribuição por NATUREZA**")
        dados_status = repo.contagem_por(df, "NATUREZA")
        grafico_status = (
            alt.Chart(dados_status)
            .mark_bar(color="#5A7D9A")
            .encode(
                x=alt.X("QUANTIDADE:Q", title="E-mails"),
                y=alt.Y("NATUREZA:N", sort="-x", title=None),
                tooltip=["NATUREZA", "QUANTIDADE"],
            )
            .properties(height=max(220, 26 * len(dados_status)))
        )
        st.altair_chart(grafico_status, use_container_width=True)

    st.divider()
    st.markdown("**Registros** — o corpo do e-mail não entra na tabela; use o detalhamento abaixo.")
    st.dataframe(
        df[repo.COLUNAS_TABELA_GERAL],
        use_container_width=True,
        hide_index=True,
        height=420,
    )

    st.divider()
    st.markdown("**Detalhar registro**")
    opcoes = {repo.rotulo_registro(linha): idx for idx, linha in df.iterrows()}
    escolha = st.selectbox("Selecione o e-mail", list(opcoes.keys()), key="sel_geral")
    if escolha:
        bloco_detalhe(df.loc[opcoes[escolha]])


# ---------------------------------------------------------------------------
# Página 2 — Auxílio Bolsa
# ---------------------------------------------------------------------------

def pagina_auxilio_bolsa(df: pd.DataFrame) -> None:
    st.subheader("Auxílio Bolsa — atenção especial (art. 25 / art. 17)")
    df_bolsa = repo.somente_auxilio_bolsa(df)

    if df_bolsa.empty:
        st.info(
            "Nenhum e-mail com CATEGORIA_ASSUNTO = 'AUXÍLIO BOLSA' nos filtros atuais."
        )
        return

    contagens = repo.contagem_prazo_bolsa(df_bolsa)
    colunas = st.columns(4)
    for coluna, codigo in zip(
        colunas, [repo.VENCIDO, repo.VENCENDO, repo.DENTRO_DO_PRAZO, repo.NAO_APURAVEL]
    ):
        coluna.markdown(cartao_prazo(codigo, contagens[codigo]), unsafe_allow_html=True)

    if contagens[repo.SEM_CLASSIFICACAO]:
        st.caption(
            f"{contagens[repo.SEM_CLASSIFICACAO]} registro(s) sem STATUS_PRAZO_ART25 "
            "preenchido na fonte — exibidos como estão, sem estimativa."
        )

    st.divider()
    st.dataframe(
        df_bolsa[repo.COLUNAS_TABELA_BOLSA],
        use_container_width=True,
        hide_index=True,
        height=380,
    )

    riscos = df_bolsa[df_bolsa["RISCO_NORMATIVO_ART17"] != ""]
    if not riscos.empty:
        st.divider()
        st.markdown(f"**Alertas normativos ({len(riscos)})** — texto integral, sem resumo")
        for _, linha in riscos.iterrows():
            with st.expander(repo.rotulo_registro(linha)):
                st.markdown(
                    f"**Servidor:** {linha['NOME_SERVIDOR'] or 'Não identificado'} · "
                    f"**Matrícula:** {linha['MATRICULA_SERVIDOR'] or '—'} · "
                    f"**Situação:** {linha['STATUS_PRAZO_ART25'] or '—'}"
                )
                st.warning(linha["RISCO_NORMATIVO_ART17"])

    st.divider()
    st.markdown("**Detalhar registro**")
    opcoes = {repo.rotulo_registro(linha): idx for idx, linha in df_bolsa.iterrows()}
    escolha = st.selectbox("Selecione o e-mail", list(opcoes.keys()), key="sel_bolsa")
    if escolha:
        bloco_detalhe(df_bolsa.loc[opcoes[escolha]])


# ---------------------------------------------------------------------------
# Página 3 — Acompanhamento (edição leve)
# ---------------------------------------------------------------------------

def pagina_acompanhamento(df: pd.DataFrame) -> None:
    st.subheader("Acompanhamento — providências e observações")
    st.caption(
        "Somente PROVIDENCIA_NECESSARIA, STATUS_PROVIDENCIA e OBSERVACOES são "
        "graváveis. A gravação é pontual, célula a célula, localizada por "
        "ID_REGISTRO — a aba nunca é reescrita em lote. Nada é salvo sem clique "
        "explícito em Salvar."
    )

    if df.empty:
        st.info("Nenhum registro corresponde aos filtros aplicados.")
        return

    colunas_visiveis = [
        "ID_REGISTRO", "DATA_HORA_ENVIO", "CATEGORIA_ASSUNTO", "ASSUNTO",
        "REMETENTE_EMAIL",
    ] + COLUNAS_EDITAVEIS

    original = df[colunas_visiveis].copy().reset_index(drop=True)

    editado = st.data_editor(
        original,
        use_container_width=True,
        hide_index=True,
        height=460,
        num_rows="fixed",
        key="editor_acompanhamento",
        disabled=[c for c in colunas_visiveis if c not in COLUNAS_EDITAVEIS],
        column_config={
            "STATUS_PROVIDENCIA": st.column_config.SelectboxColumn(
                "STATUS_PROVIDENCIA",
                options=repo.STATUS_PROVIDENCIA_OPCOES,
                required=False,
            ),
            "PROVIDENCIA_NECESSARIA": st.column_config.TextColumn(width="large"),
            "OBSERVACOES": st.column_config.TextColumn(width="large"),
            "ASSUNTO": st.column_config.TextColumn(width="medium"),
        },
    )

    alteracoes = []
    for posicao in range(len(original)):
        mudanca = {}
        for coluna in COLUNAS_EDITAVEIS:
            antes = str(original.at[posicao, coluna])
            depois = str(editado.at[posicao, coluna])
            if antes != depois:
                mudanca[coluna] = depois
        if mudanca:
            mudanca["ID_REGISTRO"] = str(original.at[posicao, "ID_REGISTRO"])
            alteracoes.append(mudanca)

    if alteracoes:
        st.markdown(f"**{len(alteracoes)} linha(s) alterada(s), ainda não gravada(s):**")
        st.dataframe(pd.DataFrame(alteracoes), use_container_width=True, hide_index=True)
    else:
        st.caption("Nenhuma alteração pendente.")

    if st.button("💾 Salvar alterações na planilha", type="primary", disabled=not alteracoes):
        try:
            gravadas = atualizar_acompanhamento(alteracoes)
        except ErroAcessoPlanilha as erro:
            st.error(str(erro))
        else:
            carregar_painel_geral.clear()
            st.success(f"{gravadas} célula(s) gravada(s) em {len(alteracoes)} linha(s).")
            st.rerun()


# ---------------------------------------------------------------------------
# Página — Demandas (visão por thread)
# ---------------------------------------------------------------------------

def pagina_demandas(df: pd.DataFrame) -> None:
    st.subheader("Demandas — uma linha por conversa, não por mensagem")
    st.caption(
        "O PAINEL_GERAL grava uma linha por MENSAGEM. Para gestão de prazo a "
        "unidade correta é a conversa (ID_THREAD): uma única thread pode "
        "responder por dezenas de linhas e distorcer qualquer indicador de carga."
    )

    demandas = repo.sem_ruido(repo.agregar_por_demanda(df))
    if demandas.empty:
        st.info("Nenhuma demanda nos filtros atuais.")
        return

    k = repo.kpis_demanda(demandas)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Demandas", k["demandas"])
    c2.metric("Sem resposta nossa", k["sem_resposta"])
    c3.metric("Com prazo vencido", k["vencidas"])
    c4.metric("Com prazo vencendo", k["vencendo"])

    somente_sem_resposta = st.checkbox("Mostrar só as que ainda não respondemos", value=False)
    if somente_sem_resposta:
        demandas = demandas[demandas["RESPONDIDA_POR_NOS"] == "NÃO"]

    st.dataframe(
        demandas[repo.COLUNAS_TABELA_DEMANDA].sort_values("ULTIMA_MENSAGEM", ascending=False),
        use_container_width=True,
        hide_index=True,
        height=460,
    )
    st.caption(
        "RESPONDIDA_POR_NOS = existe pelo menos uma mensagem enviada por "
        f"{repo.CAIXA_INSTITUCIONAL} dentro da conversa. Não significa que a "
        "demanda esteja resolvida — só que houve manifestação nossa."
    )


# ---------------------------------------------------------------------------
# Página — Prazos
# ---------------------------------------------------------------------------

def pagina_prazos(df: pd.DataFrame, df_parametros: pd.DataFrame) -> None:
    st.subheader("Prazos")

    ativos = prazos.preparar_parametros(df_parametros)
    if not ativos:
        st.warning(
            "Nenhum prazo parametrizado ainda. Enquanto a aba PARAMETROS_PRAZO não "
            "tiver linhas com ATIVO=SIM e DIAS preenchido, todo registro aparece como "
            "'Sem prazo parametrizado'. Isso é proposital: o app não arbitra prazo "
            "que não tenha base declarada."
        )

    incompletos = prazos.parametros_incompletos(df_parametros)
    if incompletos:
        st.error(
            "Tema(s) marcados ATIVO=SIM sem a coluna DIAS preenchida — ignorados no "
            "cálculo: " + ", ".join(t for t in incompletos if t)
        )

    resumo = prazos.resumo(df)
    colunas = st.columns(5)
    ordem = [
        prazos.VENCIDO, prazos.VENCENDO, prazos.DENTRO_DO_PRAZO,
        prazos.SEM_DATA_BASE, prazos.SEM_PRAZO_PARAMETRIZADO,
    ]
    for coluna, codigo in zip(colunas, ordem):
        cor = prazos.COR_SITUACAO[codigo]
        coluna.markdown(
            f"<div style='border-left:6px solid {cor};background:rgba(0,0,0,0.03);"
            f"padding:12px 16px;border-radius:6px;'>"
            f"<div style='font-size:0.72rem;color:{cor};font-weight:600;"
            f"text-transform:uppercase;'>{prazos.ROTULO_SITUACAO[codigo]}</div>"
            f"<div style='font-size:1.9rem;font-weight:700;line-height:1.1;'>"
            f"{resumo[codigo]}</div></div>",
            unsafe_allow_html=True,
        )

    if any(p["tipo_dias"] == "UTEIS" for p in ativos.values()):
        st.caption(
            "⚠️ Prazos em dias ÚTEIS excluem sábados e domingos, mas ainda não "
            "excluem feriados forenses — leia-os como estimativa otimista até que um "
            "calendário de feriados seja cadastrado."
        )

    st.divider()
    com_prazo = df[df["SITUACAO_PRAZO"].isin([prazos.VENCIDO, prazos.VENCENDO, prazos.DENTRO_DO_PRAZO])]
    if com_prazo.empty:
        st.info("Nenhum registro com prazo calculável nos filtros atuais.")
        return

    st.dataframe(
        com_prazo[[
            "ID_REGISTRO", "DATA_HORA_ENVIO", "TEMA", "ASSUNTO",
            "PRAZO_LIMITE", "DIAS_RESTANTES", "SITUACAO_PRAZO", "STATUS_PROVIDENCIA",
        ]].sort_values("DIAS_RESTANTES"),
        use_container_width=True,
        hide_index=True,
        height=400,
    )

    st.markdown("**Origem do prazo de cada registro**")
    for _, linha in com_prazo.sort_values("DIAS_RESTANTES").head(30).iterrows():
        with st.expander(f"{repo.rotulo_registro(linha)} → {linha['PRAZO_LIMITE']}"):
            st.write(linha["BASE_PRAZO"] or "—")


# ---------------------------------------------------------------------------
# Página — Ruído
# ---------------------------------------------------------------------------

def pagina_ruido(df: pd.DataFrame) -> None:
    st.subheader("Ruído — publicidade, divulgação e notificações de sistema")
    ruido = repo.apenas_ruido(df)
    st.caption(
        "Separado do fluxo de trabalho para não inflar indicador de carga. "
        "Nada é apagado: o registro continua na planilha, só sai da contagem de demanda."
    )
    if ruido.empty:
        st.info("Nenhum registro classificado como ruído nos filtros atuais.")
        return
    c1, c2 = st.columns(2)
    c1.metric("Registros de ruído", len(ruido))
    c2.metric("Proporção do total filtrado", f"{100 * len(ruido) / max(len(df), 1):.0f}%")
    st.dataframe(
        repo.contagem_por(ruido, "NATUREZA"), use_container_width=True, hide_index=True
    )
    st.dataframe(
        ruido[["ID_REGISTRO", "DATA_HORA_ENVIO", "REMETENTE_EMAIL", "ASSUNTO", "NATUREZA", "REGRA_APLICADA"]],
        use_container_width=True,
        hide_index=True,
        height=380,
    )


# ---------------------------------------------------------------------------
# Página — B.I. (visão analítica)
# ---------------------------------------------------------------------------

def pagina_bi(df: pd.DataFrame) -> None:
    st.subheader("B.I. — leitura analítica")
    if df.empty:
        st.info("Nenhum registro nos filtros atuais.")
        return

    demandas = repo.sem_ruido(repo.agregar_por_demanda(df))
    trabalho = repo.sem_ruido(df)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Mensagens", len(df))
    c2.metric("Demandas reais", len(demandas))
    c3.metric("Ruído descartável", f"{100 * len(repo.apenas_ruido(df)) / max(len(df), 1):.0f}%")
    c4.metric("Sem resposta nossa", repo.kpis_demanda(demandas)["sem_resposta"])
    c5.metric(
        "Mensagens por demanda",
        f"{len(trabalho) / max(len(demandas), 1):.1f}",
        help="Acima de 2 indica conversa longa: costuma ser demanda que voltou várias vezes.",
    )

    st.divider()
    st.markdown("### Volume ao longo do tempo")
    serie = df.dropna(subset=["_DATA_ENVIO"]).copy()
    if serie.empty:
        st.caption("Nenhuma data legível para montar a série temporal.")
    else:
        serie["DIA"] = serie["_DATA_ENVIO"].dt.date
        agregado = serie.groupby(["DIA", "E_RUIDO"]).size().reset_index(name="QUANTIDADE")
        agregado["TIPO"] = agregado["E_RUIDO"].map({"SIM": "Ruído", "NAO": "Trabalho"})
        st.altair_chart(
            alt.Chart(agregado)
            .mark_bar()
            .encode(
                x=alt.X("DIA:T", title=None),
                y=alt.Y("QUANTIDADE:Q", title="Mensagens"),
                color=alt.Color(
                    "TIPO:N",
                    title=None,
                    scale=alt.Scale(domain=["Trabalho", "Ruído"], range=[AZUL_INSTITUCIONAL, "#C7CDD3"]),
                ),
                tooltip=["DIA:T", "TIPO:N", "QUANTIDADE:Q"],
            )
            .properties(height=260),
            use_container_width=True,
        )

    st.divider()
    esquerda, direita = st.columns(2)

    with esquerda:
        st.markdown("### Assuntos por volume")
        temas = repo.contagem_por(trabalho, "TEMA")
        st.altair_chart(
            alt.Chart(temas)
            .mark_bar(color=AZUL_INSTITUCIONAL)
            .encode(
                x=alt.X("QUANTIDADE:Q", title=None),
                y=alt.Y("TEMA:N", sort="-x", title=None),
                tooltip=["TEMA", "QUANTIDADE"],
            )
            .properties(height=max(240, 24 * len(temas))),
            use_container_width=True,
        )

    with direita:
        st.markdown("### Tema × estado da demanda")
        cruzamento = (
            trabalho.groupby(["TEMA", "ESTADO_DEMANDA"]).size().reset_index(name="QUANTIDADE")
        )
        if cruzamento.empty:
            st.caption("Sem dados para cruzar.")
        else:
            st.altair_chart(
                alt.Chart(cruzamento)
                .mark_rect()
                .encode(
                    x=alt.X("ESTADO_DEMANDA:N", title=None),
                    y=alt.Y("TEMA:N", title=None),
                    color=alt.Color("QUANTIDADE:Q", scale=alt.Scale(scheme="blues"), title=None),
                    tooltip=["TEMA", "ESTADO_DEMANDA", "QUANTIDADE"],
                )
                .properties(height=max(240, 24 * trabalho["TEMA"].nunique())),
                use_container_width=True,
            )

    st.divider()
    st.markdown("### Quem mais demanda a Coordenadoria")
    remetentes = (
        trabalho[trabalho["NATUREZA"] != "RESPOSTA_NOSSA"]["REMETENTE_EMAIL"]
        .replace("", "(sem remetente)")
        .value_counts()
        .head(15)
        .rename_axis("REMETENTE")
        .reset_index(name="QUANTIDADE")
    )
    st.altair_chart(
        alt.Chart(remetentes)
        .mark_bar(color="#5A7D9A")
        .encode(
            x=alt.X("QUANTIDADE:Q", title=None),
            y=alt.Y("REMETENTE:N", sort="-x", title=None),
            tooltip=["REMETENTE", "QUANTIDADE"],
        )
        .properties(height=max(240, 24 * len(remetentes))),
        use_container_width=True,
    )

    st.divider()
    st.markdown("### Resumo para compartilhar")
    st.caption(
        "Texto pronto para colar em e-mail, ofício ou mensagem. Todos os números "
        "vêm do filtro atual — nenhum é estimado."
    )
    st.code(_resumo_textual(df, demandas), language=None)

    st.download_button(
        "⬇️ Baixar CSV do recorte atual",
        data=df[repo.COLUNAS_TABELA_GERAL].to_csv(index=False).encode("utf-8-sig"),
        file_name="painel_cocarreira_recorte.csv",
        mime="text/csv",
    )


def _resumo_textual(df: pd.DataFrame, demandas: pd.DataFrame) -> str:
    validas = df["_DATA_ENVIO"].dropna()
    periodo = (
        f"{validas.min():%d/%m/%Y} a {validas.max():%d/%m/%Y}" if not validas.empty else "período não apurável"
    )
    k = repo.kpis_demanda(demandas)
    trabalho = repo.sem_ruido(df)
    ruido = repo.apenas_ruido(df)
    principais = repo.contagem_por(trabalho, "TEMA").head(5)
    linhas_tema = "\n".join(
        f"  - {linha['TEMA']}: {linha['QUANTIDADE']}" for _, linha in principais.iterrows()
    )
    return (
        f"PAINEL COCARREIRA — {periodo}\n"
        f"Mensagens recebidas: {len(df)}\n"
        f"Demandas efetivas (conversas): {k['demandas']}\n"
        f"Sem resposta da Coordenadoria: {k['sem_resposta']}\n"
        f"Com prazo vencido: {k['vencidas']} | vencendo: {k['vencendo']}\n"
        f"Mensagens sem demanda (publicidade/divulgação/sistema): {len(ruido)}\n"
        f"Principais assuntos:\n{linhas_tema}"
    )


# ---------------------------------------------------------------------------
# Barra lateral e roteamento
# ---------------------------------------------------------------------------

def main() -> None:
    st.title("📬 Painel Geral de E-mails — COCARREIRA")
    st.caption(
        "Corregedoria-Geral do Foro Extrajudicial do Maranhão · TJMA — "
        "leitura da aba PAINEL_GERAL alimentada pelo Apps Script da conta "
        "cocarreira@tjma.jus.br."
    )

    try:
        parametros = carregar_parametros()
        df_completo = carregar(parametros)
    except ErroAcessoPlanilha as erro:
        st.error(str(erro))
        st.info(
            "Compartilhe a planilha com a Service Account como Leitor (ou Editor, "
            "se for usar a página de Acompanhamento):\n\n"
            f"`{email_service_account()}`"
        )
        st.stop()
        return

    if parametros["DE_PARA_LABELS"].empty and parametros["REGRAS_CLASSIFICACAO"].empty:
        st.warning(
            "As abas de parâmetro ainda não existem na planilha. Sem elas, NATUREZA, "
            "TEMA e ESTADO_DEMANDA ficam todos em NAO_CLASSIFICADO. Cole as abas do "
            "arquivo PAINEL_COCARREIRA_ABAS_NOVAS.xlsx na planilha para ativar a "
            "classificação."
        )

    with st.sidebar:
        st.header("Filtros")

        if st.button("🔄 Recarregar da planilha", use_container_width=True):
            carregar_painel_geral.clear()
            limpar_cache_parametros()
            st.rerun()

        escopo = st.radio(
            "Escopo",
            ["Só trabalho (sem ruído)", "Tudo", "Só ruído"],
            help="Ruído = publicidade externa, divulgação institucional e notificação de sistema.",
        )

        naturezas = st.multiselect(
            "Natureza", sorted(v for v in df_completo["NATUREZA"].unique() if v)
        )
        temas = st.multiselect("Tema", sorted(v for v in df_completo["TEMA"].unique() if v))
        situacoes = st.multiselect(
            "Situação do prazo", sorted(v for v in df_completo["SITUACAO_PRAZO"].unique() if v)
        )

        categorias_presentes = sorted(v for v in df_completo["CATEGORIA_ASSUNTO"].unique() if v)
        categorias = st.multiselect("CATEGORIA_ASSUNTO (fonte)", categorias_presentes)
        status_presentes = sorted(v for v in df_completo["STATUS_TRATAMENTO"].unique() if v)
        status = st.multiselect("STATUS_TRATAMENTO (fonte)", status_presentes)

        limites = repo.limites_de_data(df_completo)
        intervalo, incluir_sem_data = None, True
        if limites:
            inicio_padrao, fim_padrao = limites
            escolha = st.date_input(
                "Período de envio",
                value=(inicio_padrao.date(), fim_padrao.date()),
                min_value=inicio_padrao.date(),
                max_value=fim_padrao.date(),
            )
            if isinstance(escolha, tuple) and len(escolha) == 2:
                intervalo = (
                    pd.Timestamp(escolha[0]),
                    pd.Timestamp(escolha[1]) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1),
                )
            incluir_sem_data = st.checkbox("Incluir registros sem data legível", value=True)
        else:
            st.caption("Nenhuma DATA_HORA_ENVIO legível na fonte — filtro de período desativado.")

        busca = st.text_input("Busca livre (assunto / e-mail do remetente)")
        apenas_pendentes = st.checkbox("Somente providências pendentes", value=False)

        st.divider()
        st.divider()
        link_gem = st.secrets.get("gem", {}).get("url", "") if hasattr(st, "secrets") else ""
        if link_gem:
            st.link_button("🤖 Abrir assistente (Gem)", link_gem, use_container_width=True)

        pagina = st.radio(
            "Página",
            ["🚦 Alertas", "Dashboard geral", "B.I.", "Demandas", "Prazos", "Auxílio Bolsa", "Acompanhamento", "Ruído"],
            label_visibility="collapsed",
        )

        with st.expander("Diagnóstico"):
            st.write(f"Mensagens lidas: **{len(df_completo)}**")
            st.write(f"Conversas (threads): **{df_completo['ID_THREAD'].nunique()}**")
            medidas = cls.cobertura(df_completo)
            st.write(f"TEMA resolvido: **{medidas['TEMA']}%** (via label: {medidas['por_label']}%)")
            st.write(f"NATUREZA resolvida: **{medidas['NATUREZA']}%**")
            invalidas = cls.regras_invalidas(
                cls.preparar_regras(parametros.get("REGRAS_CLASSIFICACAO"))
            )
            if invalidas:
                st.error("Regra com regex inválida: " + "; ".join(invalidas))
            sem_data = int(df_completo["_DATA_ENVIO"].isna().sum())
            if sem_data:
                st.write(f"Sem DATA_HORA_ENVIO legível: **{sem_data}**")
            st.write("Service Account:")
            st.code(email_service_account(), language=None)
            st.caption("Cache de leitura: 300 s.")

    df_filtrado = repo.aplicar_filtros(
        df_completo,
        categorias=categorias,
        status_tratamento=status,
        intervalo_datas=intervalo,
        incluir_sem_data=incluir_sem_data,
        busca_livre=busca,
        apenas_pendentes=apenas_pendentes,
    )
    df_filtrado = repo.filtrar_por_eixo(df_filtrado, "NATUREZA", naturezas)
    df_filtrado = repo.filtrar_por_eixo(df_filtrado, "TEMA", temas)
    df_filtrado = repo.filtrar_por_eixo(df_filtrado, "SITUACAO_PRAZO", situacoes)

    if escopo == "Só trabalho (sem ruído)":
        df_escopo = repo.sem_ruido(df_filtrado)
    elif escopo == "Só ruído":
        df_escopo = repo.apenas_ruido(df_filtrado)
    else:
        df_escopo = df_filtrado

    if pagina == "🚦 Alertas":
        pagina_alertas(df_escopo)
    elif pagina == "Dashboard geral":
        pagina_dashboard(df_escopo, df_completo)
    elif pagina == "B.I.":
        pagina_bi(df_escopo if escopo != "Só trabalho (sem ruído)" else df_filtrado)
    elif pagina == "Demandas":
        pagina_demandas(df_escopo)
    elif pagina == "Prazos":
        pagina_prazos(df_escopo, parametros.get("PARAMETROS_PRAZO"))
    elif pagina == "Auxílio Bolsa":
        pagina_auxilio_bolsa(df_escopo)
    elif pagina == "Acompanhamento":
        pagina_acompanhamento(df_escopo)
    else:
        pagina_ruido(df_filtrado)


if __name__ == "__main__":
    main()
