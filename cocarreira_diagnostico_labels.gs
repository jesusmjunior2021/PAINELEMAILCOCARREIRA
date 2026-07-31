/**
 * cocarreira_diagnostico_labels.gs
 *
 * ONDE RODAR: mesmo projeto Apps Script de cocarreira_painel_geral_captura.gs,
 * na conta cocarreira@tjma.jus.br.
 *
 * POR QUE EXISTE: dos 109 registros já capturados em PAINEL_GERAL, 106 vieram
 * com LABELS_GMAIL VAZIO e 100% caíram em CATEGORIA_ASSUNTO = "OUTROS". O único
 * label que apareceu foi "Auxílio-Bolsa/Processado" — que não existe em
 * MAPA_LABEL_PARA_CATEGORIA. Antes de reescrever qualquer mapeamento, é preciso
 * saber os NOMES EXATOS dos labels como a API os devolve (com o caminho de
 * aninhamento) e quantas threads da CAIXA DE ENTRADA realmente os têm.
 *
 * Estas funções NÃO alteram e-mail, NÃO alteram PAINEL_GERAL e NÃO classificam
 * nada. Só medem e escrevem em abas novas de diagnóstico.
 *
 * COMO USAR:
 *   1. Rodar listarLabelsReais()      -> cria/atualiza a aba LABELS_REAIS
 *   2. Rodar diagnosticarCobertura()  -> cria/atualiza a aba DIAGNOSTICO_LABELS
 *   3. Usar a coluna NOME_EXATO de LABELS_REAIS para preencher DE_PARA_LABELS.
 */

var ID_PLANILHA_DIAG = "1E275cdQIxfJY20GmpxQr6rDtqkEk7eUl-bTg_dRqiTc";

/**
 * Lista TODOS os labels do usuário com o nome exato devolvido pela API.
 * Labels aninhados aparecem como "Pai/Filho" — é esse texto, e só ele, que
 * thread.getLabels() retorna e que DE_PARA_LABELS precisa conter.
 */
function listarLabelsReais() {
  var planilha = SpreadsheetApp.openById(ID_PLANILHA_DIAG);
  var aba = _abaLimpa(planilha, "LABELS_REAIS", [
    "NOME_EXATO", "TEM_BARRA_ANINHAMENTO", "THREADS_NA_INBOX", "THREADS_TOTAL", "OBSERVACAO",
  ]);

  var labels = GmailApp.getUserLabels();
  var linhas = [];

  labels.forEach(function (label) {
    var nome = label.getName();
    var totalInbox = 0;
    try {
      // Contagem limitada a 500 threads: suficiente para saber se o label é usado.
      totalInbox = GmailApp.search('in:inbox label:"' + nome + '"', 0, 500).length;
    } catch (e) {
      totalInbox = -1;
    }
    var total = 0;
    try {
      total = GmailApp.search('label:"' + nome + '"', 0, 500).length;
    } catch (e) {
      total = -1;
    }
    var obs = "";
    if (totalInbox === 0 && total > 0) {
      obs = "Label usado, mas nenhuma thread dele está na INBOX — a captura atual " +
            "(query 'in:inbox') NUNCA vai enxergar este label.";
    } else if (total === 0) {
      obs = "Label existe mas não tem thread — candidato a desativar em DE_PARA_LABELS.";
    }
    linhas.push([nome, nome.indexOf("/") !== -1 ? "SIM" : "NAO", totalInbox, total, obs]);
  });

  if (linhas.length > 0) {
    aba.getRange(2, 1, linhas.length, 5).setValues(linhas);
  }
  Logger.log(linhas.length + " label(s) listado(s) em LABELS_REAIS.");
}

/**
 * Mede quantas threads da caixa de entrada têm ALGUM label conhecido.
 * É esta função que responde à pergunta central: o problema é o mapeamento
 * estar errado, ou os e-mails simplesmente não estarem etiquetados na inbox?
 */
function diagnosticarCobertura() {
  var planilha = SpreadsheetApp.openById(ID_PLANILHA_DIAG);
  var aba = _abaLimpa(planilha, "DIAGNOSTICO_LABELS", [
    "ASSUNTO", "REMETENTE", "DATA", "QTD_LABELS", "LABELS_EXATOS",
  ]);

  var threads = GmailApp.search("in:inbox newer_than:60d", 0, 300);
  var comLabel = 0, semLabel = 0;
  var linhas = [];

  threads.forEach(function (thread) {
    var nomes = thread.getLabels().map(function (l) { return l.getName(); });
    if (nomes.length > 0) { comLabel++; } else { semLabel++; }
    var primeira = thread.getMessages()[0];
    linhas.push([
      thread.getFirstMessageSubject(),
      primeira.getFrom(),
      primeira.getDate(),
      nomes.length,
      nomes.join(" | "),
    ]);
  });

  if (linhas.length > 0) {
    aba.getRange(2, 1, linhas.length, 5).setValues(linhas);
  }

  var total = comLabel + semLabel;
  var pct = total > 0 ? Math.round(1000 * comLabel / total) / 10 : 0;
  Logger.log(
    "Threads na inbox (60d): " + total +
    " | COM label: " + comLabel + " (" + pct + "%)" +
    " | SEM label: " + semLabel + ".\n" +
    "Se a fatia SEM label for alta, o gargalo NÃO é o mapeamento: é que a " +
    "etiquetagem acontece fora da caixa de entrada (ou depois do arquivamento). " +
    "Nesse caso, ou a query de captura deixa de usar 'in:inbox', ou a " +
    "classificação passa a depender das REGRAS_CLASSIFICACAO da planilha."
  );
}

function _abaLimpa(planilha, nome, cabecalho) {
  var aba = planilha.getSheetByName(nome);
  if (!aba) { aba = planilha.insertSheet(nome); }
  aba.clear();
  aba.getRange(1, 1, 1, cabecalho.length).setValues([cabecalho]);
  aba.getRange(1, 1, 1, cabecalho.length).setFontWeight("bold");
  aba.setFrozenRows(1);
  return aba;
}

/**
 * ------------------------------------------------------------------------
 * CORREÇÕES A APLICAR EM cocarreira_painel_geral_captura.gs
 * (não são executáveis aqui — são o patch a ser feito no arquivo original)
 * ------------------------------------------------------------------------
 *
 * 1) LABEL ANINHADO NUNCA CASA. As chaves abaixo, hoje em
 *    MAPA_LABEL_PARA_CATEGORIA, são nomes de FILHO sem o pai e por isso
 *    jamais serão encontradas por thread.getLabels():
 *        "Propaganda"                 -> na verdade "COORDENADORIA/Propaganda"
 *        "Estágio"                    -> na verdade "ESTAGIÁRIOS/Estágio"
 *        "Notas Fiscais e Certidões"  -> na verdade "CURSOS/Notas Fiscais e Certidões"
 *        "Turma 4 e 5 outubro"        -> na verdade "CURSOS/Turma 4 e 5 outubro"
 *        "Turma 13 e 14 outubro"      -> idem
 *        "Turma 22 e 23 setembro"     -> idem
 *        "Turma 29 e 30 setembro"     -> idem
 *        "Contatos"                   -> existe pai E filho "Contatos/Contatos"
 *    Confirmar cada caminho na aba LABELS_REAIS antes de corrigir.
 *
 * 2) FALTA "Auxílio-Bolsa/Processado" — é o ÚNICO label presente nos dados
 *    reais (3 registros) e não está no mapa, então esses 3 caíram em OUTROS.
 *
 * 3) "Não apurável" VAZANDO PARA CATEGORIA NÃO-BOLSA. Em
 *    _processarEGravarThreads(), estas duas variáveis são inicializadas
 *    ANTES do teste de categoria:
 *        var dataTermino = "Não apurável", prazoLimite = "Não apurável";
 *    Resultado: os 109 registros gravaram "Não apurável" em
 *    DATA_TERMINO_CURSO e PRAZO_LIMITE_ART25, inclusive publicidade externa.
 *    O documento MAT-001 §3.1 diz que essas colunas devem ficar VAZIAS fora
 *    do Auxílio Bolsa. Corrigir para:
 *        var dataTermino = "", prazoLimite = "";
 *    e atribuir "Não apurável" apenas dentro do bloco
 *    `if (categoria === "AUXÍLIO BOLSA")`.
 *
 * 4) MAPA FIXO NO CÓDIGO. Depois de validado, MAPA_LABEL_PARA_CATEGORIA e
 *    MAPA_LABEL_PARA_STATUS devem ser lidos da aba DE_PARA_LABELS, para que
 *    a equipe altere a classificação sem mexer em código.
 */
