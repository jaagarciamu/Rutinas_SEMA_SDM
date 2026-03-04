/**
 * Ejemplo base para extraer correos y registrar metadatos en Google Sheets.
 * Reemplazar IDs y filtros según reglas de negocio.
 */
function processInboxToSheet() {
  var sheetId = PropertiesService.getScriptProperties().getProperty('SHEET_ID');
  if (!sheetId) {
    throw new Error('Falta definir SHEET_ID en Script Properties');
  }

  var sheet = SpreadsheetApp.openById(sheetId).getActiveSheet();
  var threads = GmailApp.search('label:inbox newer_than:1d');

  threads.forEach(function(thread) {
    var message = thread.getMessages().pop();
    sheet.appendRow([
      new Date(),
      message.getFrom(),
      message.getSubject(),
      message.getDate()
    ]);
  });
}
