"""
servico_modelos_resposta.py — modelos de resposta padrão, parametrizados na
planilha (aba MODELOS_RESPOSTA), mesmo padrão de DE_PARA_LABELS.

PRINCÍPIO: o app SUGERE um modelo de resposta pronto pra revisão humana —
nunca envia e-mail sozinho. Isso já é o que você definiu: "resposta
automatizada com aprovação humana ou não dependendo do tipo de mala
direta". Este módulo só prepara o texto; o disparo é decisão manual de
vocês (Gmail, mala direta, etc.), fora do escopo deste código.

ESTRUTURA DA ABA MODELOS_RESPOSTA (colar na planilha, mesmo padrão de
DE_PARA_LABELS/REGRAS_CLASSIFICACAO):

  CHAVE | TEMA_ALVO | ATIVO | ASSUNTO_SUGERIDO | CORPO_MODELO

  - CHAVE: identificador curto do modelo (ex.: RENOVACAO_LEMBRETE_PRAZO).
  - TEMA_ALVO: qual TEMA (eixo de classificador.py) este modelo se aplica.
    Vazio = modelo genérico, aparece pra qualquer TEMA.
  - ATIVO: SIM/NAO — modelo desativado não aparece como sugestão.
  - ASSUNTO_SUGERIDO: assunto pronto pro rascunho.
  - CORPO_MODELO: corpo do e-mail, com placeholders entre chaves, ex.:
    {NOME_SERVIDOR}, {DATA_HORA_ENVIO}, {PRAZO_LIMITE}. Placeholder sem
    correspondência na linha vira "" — nunca é inventado.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

# Modelo inicial (o que você colou) — cole isto na aba MODELOS_RESPOSTA da
# planilha via _linha_modelo_padrao() abaixo, ou direto na planilha à mão.
CHAVE_RENOVACAO_LEMBRETE = "RENOVACAO_LEMBRETE_PRAZO"

CORPO_RENOVACAO_LEMBRETE = """Prezados(as) beneficiários(as) do Auxílio-Bolsa de Estudos,

A Coordenadoria de Acompanhamento e Desenvolvimento na Carreira (CAEDNC) informa que hoje é o último dia para o cadastramento, no DIGIDOC, da requisição de renovação do Auxílio-Bolsa de Estudos, acompanhada da documentação exigida pela Resolução-GP nº 1/2023.

Caso a requisição de renovação já tenha sido devidamente cadastrada no sistema DIGIDOC, solicitamos que desconsidere o teor deste e-mail, uma vez que a presente comunicação possui caráter meramente preventivo e foi encaminhada aos beneficiários como medida de apoio ao cumprimento do prazo regulamentar.

Nos termos da Resolução-GP nº 1/2023, compete exclusivamente ao beneficiário observar os prazos e providenciar, tempestivamente, a formalização do pedido de renovação, com a apresentação da documentação comprobatória necessária. A saber:

Art. 14, § 1º Para a revisão e a renovação previstas no caput deste artigo, a beneficiária ou o beneficiário deverá apresentar, até o último dia útil dos meses de janeiro e julho de cada ano, a regularização acadêmica do curso, com os respectivos comprovantes de pagamentos efetuados à IES, do semestre concluído e/ou em andamento, a descrição do valor efetivamente pago; e o histórico curricular de todo o curso com notas e/ou a declaração das disciplinas cursadas até o período vigente, com a indicação de status acadêmico.

Desse modo, as requisições cadastradas após o encerramento do prazo não serão recebidas nem processadas, em observância aos princípios da legalidade, da isonomia e da vinculação às normas administrativas.

Embora o acompanhamento dos prazos seja de responsabilidade do servidor, a CAEDNC encaminha este e-mail como um lembrete institucional, visando evitar prejuízos decorrentes da perda do prazo.

Aproveitamos a oportunidade para reforçar que o Auxílio-Bolsa de Estudos constitui uma política institucional de incentivo à qualificação profissional dos servidores, não possuindo natureza remuneratória ou alimentar. Sua manutenção está condicionada ao cumprimento dos requisitos previstos na regulamentação, à regular tramitação administrativa e à disponibilidade orçamentária.

Permanecemos à disposição para prestar os esclarecimentos necessários por meio dos canais oficiais de atendimento.

Atenciosamente,
Coordenadoria de Acompanhamento e Desenvolvimento na Carreira – CAEDNC
Diretoria de Recursos Humanos – DRH
Tribunal de Justiça do Estado do Maranhão"""

# ATENÇÃO: este modelo cita "Resolução-GP nº 1/2023", Art. 14, § 1º — texto
# diferente do que está na RESOL-GP 182021 (Art. 3º, § 3º) que você também
# mandou. Antes de usar isso em produção, confirme qual resolução está
# vigente hoje pra esse prazo específico (ver aviso no chat).


def _linha_modelo_padrao() -> Dict[str, str]:
    return {
        "CHAVE": CHAVE_RENOVACAO_LEMBRETE,
        "TEMA_ALVO": "",  # genérico até vocês definirem o TEMA de Auxílio Bolsa/renovação
        "ATIVO": "SIM",
        "ASSUNTO_SUGERIDO": "Lembrete — Prazo de renovação do Auxílio-Bolsa de Estudos",
        "CORPO_MODELO": CORPO_RENOVACAO_LEMBRETE,
    }


def _texto(valor) -> str:
    return "" if valor is None else str(valor).strip()


def preparar_modelos(df: Optional[pd.DataFrame]) -> List[Dict[str, str]]:
    """Modelos ATIVOS da aba MODELOS_RESPOSTA. Se a aba não existir ainda,
    devolve só o modelo padrão embutido (não perde a funcionalidade
    enquanto a aba não é colada na planilha)."""
    if df is None or df.empty:
        return [_linha_modelo_padrao()]

    modelos = []
    for _, linha in df.iterrows():
        if _texto(linha.get("ATIVO")).upper() not in ("SIM", "S", "TRUE", "1"):
            continue
        chave = _texto(linha.get("CHAVE"))
        corpo = _texto(linha.get("CORPO_MODELO"))
        if not chave or not corpo:
            continue
        modelos.append({
            "CHAVE": chave,
            "TEMA_ALVO": _texto(linha.get("TEMA_ALVO")),
            "ASSUNTO_SUGERIDO": _texto(linha.get("ASSUNTO_SUGERIDO")),
            "CORPO_MODELO": corpo,
        })
    return modelos or [_linha_modelo_padrao()]


def modelos_aplicaveis(modelos: List[Dict[str, str]], tema: str) -> List[Dict[str, str]]:
    """Modelos genéricos (TEMA_ALVO vazio) + os específicos do TEMA da linha."""
    return [m for m in modelos if not m.get("TEMA_ALVO") or m["TEMA_ALVO"] == tema]


def preencher_modelo(modelo: Dict[str, str], linha: pd.Series) -> Dict[str, str]:
    """
    Substitui placeholders {CAMPO} pelos valores da linha do painel.
    Placeholder sem coluna correspondente vira "" — nunca inventa dado.
    """
    def _substituir(texto: str) -> str:
        resultado = texto
        import re
        for placeholder in set(re.findall(r"\{([A-Z0-9_]+)\}", texto)):
            valor = _texto(linha.get(placeholder, ""))
            resultado = resultado.replace("{" + placeholder + "}", valor)
        return resultado

    return {
        "CHAVE": modelo["CHAVE"],
        "ASSUNTO": _substituir(modelo.get("ASSUNTO_SUGERIDO", "")),
        "CORPO": _substituir(modelo.get("CORPO_MODELO", "")),
    }
