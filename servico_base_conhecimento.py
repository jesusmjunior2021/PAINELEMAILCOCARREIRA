"""
servico_base_conhecimento.py — a base normativa que dá lastro às respostas.

O PROBLEMA QUE ISTO RESOLVE: um modelo de linguagem sabe escrever bonito e
não sabe a Resolução-GP nº 1/2023. Se você pedir a ele que responda a um
servidor sobre prazo de Auxílio Bolsa sem entregar o texto da norma, ele vai
inventar artigo, número e prazo — com aparência perfeitamente convincente.
Num ofício da Coordenadoria, isso é erro administrativo assinado.

A solução é grounding: a IA só pode usar o que estiver NESTA base, e cada
resposta precisa apontar de qual documento saiu. Sem documento cadastrado
para o tema, não há resposta gerada — há um aviso de que falta norma.

ONDE OS DOCUMENTOS MORAM: aba BASE_CONHECIMENTO da própria planilha, mesmo
padrão de DE_PARA_LABELS. Fica versionado junto com o resto, a equipe edita
sem programar, e o Apps Script/Gem enxergam o mesmo conteúdo.

  ID_DOC | TITULO | TEMA_ALVO | TIPO | VIGENCIA | FONTE | ATIVO | ORDEM | CONTEUDO_MD

  - ID_DOC     identificador curto (RESGP-1-2023). Documento partido em
               vários pedaços repete o ID e varia a ORDEM.
  - TEMA_ALVO  a qual TEMA este documento se aplica. Vazio = vale para todos.
               Vários temas separados por vírgula.
  - TIPO       RESOLUCAO | LEI | PROCEDIMENTO | FAQ | MODELO_OFICIO
  - VIGENCIA   texto livre ("desde 01/2023", "revogada em 06/2025").
               Documento revogado fica com ATIVO=NAO, nunca é apagado.
  - FONTE      link ou referência de onde veio o texto.
  - CONTEUDO_MD  o texto em Markdown.

LIMITE DE CÉLULA: 50.000 caracteres no Google Sheets. Uma resolução inteira
costuma caber; quando não cabe, `fatiar_markdown()` parte em pedaços de
~40.000 respeitando os títulos, e cada pedaço vira uma linha com a mesma
ID_DOC e ORDEM crescente.

O QUE ESTE MÓDULO NÃO FAZ: não interpreta norma, não decide qual dispositivo
prevalece, não resume. Ele recupera trecho e devolve com a fonte colada.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

ABA_BASE = "BASE_CONHECIMENTO"
LIMITE_CELULA = 40000

COLUNAS_BASE = [
    "ID_DOC", "TITULO", "TEMA_ALVO", "TIPO", "VIGENCIA",
    "FONTE", "ATIVO", "ORDEM", "CONTEUDO_MD",
]

TIPOS_VALIDOS = ["RESOLUCAO", "LEI", "PROCEDIMENTO", "FAQ", "MODELO_OFICIO"]


def _texto(valor) -> str:
    return "" if valor is None else str(valor).strip()


def _ativo(valor) -> bool:
    return _texto(valor).upper() in ("SIM", "S", "TRUE", "1")


# ---------------------------------------------------------------------------
# Ingestão de .md
# ---------------------------------------------------------------------------

def fatiar_markdown(conteudo: str, limite: int = LIMITE_CELULA) -> List[str]:
    """
    Parte o texto em pedaços que caibam na célula, cortando em título de
    seção sempre que possível — para que nenhum artigo fique partido no meio
    e o trecho recuperado continue fazendo sentido sozinho.
    """
    texto = (conteudo or "").strip()
    if len(texto) <= limite:
        return [texto] if texto else []

    blocos = re.split(r"(?m)^(?=#{1,6}\s)", texto)
    pedacos, atual = [], ""

    for bloco in blocos:
        if len(atual) + len(bloco) <= limite:
            atual += bloco
            continue
        if atual:
            pedacos.append(atual.strip())
            atual = ""
        # Bloco isolado ainda maior que o limite: corta por parágrafo.
        while len(bloco) > limite:
            corte = bloco.rfind("\n\n", 0, limite)
            if corte <= 0:
                corte = limite
            pedacos.append(bloco[:corte].strip())
            bloco = bloco[corte:]
        atual = bloco

    if atual.strip():
        pedacos.append(atual.strip())
    return [p for p in pedacos if p]


def montar_linhas(
    id_doc: str,
    titulo: str,
    conteudo_md: str,
    tema_alvo: str = "",
    tipo: str = "RESOLUCAO",
    vigencia: str = "",
    fonte: str = "",
) -> List[Dict[str, str]]:
    """Transforma um .md em linhas prontas para a aba BASE_CONHECIMENTO."""
    pedacos = fatiar_markdown(conteudo_md)
    return [
        {
            "ID_DOC": id_doc,
            "TITULO": titulo if len(pedacos) == 1 else f"{titulo} (parte {i + 1}/{len(pedacos)})",
            "TEMA_ALVO": tema_alvo,
            "TIPO": tipo,
            "VIGENCIA": vigencia,
            "FONTE": fonte,
            "ATIVO": "SIM",
            "ORDEM": str(i + 1),
            "CONTEUDO_MD": pedaco,
        }
        for i, pedaco in enumerate(pedacos)
    ]


# ---------------------------------------------------------------------------
# Leitura e recuperação
# ---------------------------------------------------------------------------

def preparar(df: Optional[pd.DataFrame]) -> List[Dict[str, str]]:
    """Documentos ativos, ordenados por ID_DOC e ORDEM."""
    if df is None or df.empty:
        return []
    documentos = []
    for _, linha in df.iterrows():
        if not _ativo(linha.get("ATIVO")):
            continue
        conteudo = _texto(linha.get("CONTEUDO_MD"))
        if not conteudo:
            continue
        try:
            ordem = int(float(_texto(linha.get("ORDEM")) or 1))
        except ValueError:
            ordem = 1
        documentos.append(
            {
                "id_doc": _texto(linha.get("ID_DOC")),
                "titulo": _texto(linha.get("TITULO")),
                "temas": [t.strip().upper() for t in _texto(linha.get("TEMA_ALVO")).split(",") if t.strip()],
                "tipo": _texto(linha.get("TIPO")).upper(),
                "vigencia": _texto(linha.get("VIGENCIA")),
                "fonte": _texto(linha.get("FONTE")),
                "ordem": ordem,
                "conteudo": conteudo,
            }
        )
    return sorted(documentos, key=lambda d: (d["id_doc"], d["ordem"]))


def documentos_do_tema(documentos: List[Dict[str, str]], tema: str) -> List[Dict[str, str]]:
    """Documentos do tema + os genéricos (TEMA_ALVO vazio)."""
    alvo = (tema or "").strip().upper()
    return [d for d in documentos if not d["temas"] or alvo in d["temas"]]


def _pontuar(conteudo: str, termos: List[str]) -> int:
    texto = conteudo.lower()
    return sum(texto.count(termo) for termo in termos)


def recuperar_contexto(
    documentos: List[Dict[str, str]],
    tema: str,
    consulta: str = "",
    max_trechos: int = 4,
    max_caracteres: int = 24000,
) -> List[Dict[str, str]]:
    """
    Seleciona os trechos que vão para o modelo. Busca por termo simples
    (contagem de ocorrência), não embedding: é previsível, não custa chamada
    de API e, para acervo normativo de um setor, funciona bem. Se nada casar
    com a consulta, devolve os documentos do tema na ordem cadastrada.
    """
    candidatos = documentos_do_tema(documentos, tema)
    if not candidatos:
        return []

    termos = [t for t in re.findall(r"[a-zà-ú0-9]{4,}", (consulta or "").lower())][:12]
    if termos:
        pontuados = [(d, _pontuar(d["conteudo"], termos)) for d in candidatos]
        pontuados = [p for p in pontuados if p[1] > 0]
        if pontuados:
            pontuados.sort(key=lambda p: p[1], reverse=True)
            candidatos = [p[0] for p in pontuados]

    selecionados, total = [], 0
    for documento in candidatos[:max_trechos]:
        conteudo = documento["conteudo"]
        if total + len(conteudo) > max_caracteres:
            conteudo = conteudo[: max(0, max_caracteres - total)]
        if not conteudo:
            break
        selecionados.append({**documento, "conteudo": conteudo})
        total += len(conteudo)
    return selecionados


def formatar_contexto(trechos: List[Dict[str, str]]) -> str:
    """Monta o bloco que vai no prompt, com a fonte colada em cada trecho."""
    partes = []
    for t in trechos:
        cabecalho = f"[{t['id_doc']} — {t['titulo']}]"
        if t["vigencia"]:
            cabecalho += f" (vigência: {t['vigencia']})"
        if t["fonte"]:
            cabecalho += f" (fonte: {t['fonte']})"
        partes.append(f"{cabecalho}\n{t['conteudo']}")
    return "\n\n---\n\n".join(partes)


def cobertura_por_tema(documentos: List[Dict[str, str]], temas: List[str]) -> pd.DataFrame:
    """
    Quais temas já têm norma cadastrada e quais estão descobertos. É o mapa
    do que ainda impede a geração de resposta com lastro.
    """
    linhas = []
    for tema in sorted(set(temas)):
        do_tema = [d for d in documentos if tema.strip().upper() in d["temas"]]
        genericos = [d for d in documentos if not d["temas"]]
        linhas.append(
            {
                "TEMA": tema,
                "DOCS_ESPECÍFICOS": len(do_tema),
                "DOCS_GENÉRICOS": len(genericos),
                "PODE_GERAR_RESPOSTA": "SIM" if (do_tema or genericos) else "NÃO",
                "DOCUMENTOS": ", ".join(sorted({d["id_doc"] for d in do_tema})) or "—",
            }
        )
    return pd.DataFrame(linhas)


def linhas_para_planilha(linhas: List[Dict[str, str]]) -> List[List[str]]:
    return [[linha.get(coluna, "") for coluna in COLUNAS_BASE] for linha in linhas]
