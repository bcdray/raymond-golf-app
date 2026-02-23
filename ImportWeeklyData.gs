/**
 * Google Apps Script — Auto-Import Weekly Golf Pool Data
 *
 * Attach this script to the "2026 Standings" Google Sheet via
 * Extensions > Apps Script. No advanced services needed.
 *
 * Functions:
 *   onOpen()            — Adds "Golf Pool > Import Latest Email" menu item
 *   importWeeklyData()  — Main import: Gmail > .xls > convert > update sheet
 *   testConvert()       — Debug: test .xls conversion in isolation
 */

// ── Configuration ──────────────────────────────────────────────────────────────
var SENDER_EMAIL   = "gtmagao@gmail.com";
var SHEET_NAME     = "2026 Standings";
var GMAIL_LABEL    = "Golf-Processed";
var DATA_START_ROW = 3; // 1-indexed row where tournament names begin (row 3)

// ── Menu ───────────────────────────────────────────────────────────────────────

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("Golf Pool")
    .addItem("Import Latest Email", "importWeeklyData")
    .addToUi();
}

// ── Main Import ────────────────────────────────────────────────────────────────

function importWeeklyData() {
  // Detect if running from a menu (interactive) or a trigger (background)
  var ui = null;
  try {
    ui = SpreadsheetApp.getUi();
  } catch (e) {
    // Running from a trigger — no UI available
  }

  function notify(msg) {
    Logger.log(msg);
    if (ui) {
      ui.alert(msg);
    }
  }

  // 1. Find unprocessed emails with .xls attachment
  var query = "from:" + SENDER_EMAIL + " has:attachment -label:" + GMAIL_LABEL;
  var threads = GmailApp.search(query, 0, 10);

  if (threads.length === 0) {
    notify("No new emails found from " + SENDER_EMAIL + " with attachments.");
    return;
  }

  // Use the most recent thread
  var thread = threads[0];
  var messages = thread.getMessages();
  var attachment = null;
  var message = null;

  // Search messages newest-first for an .xls attachment
  for (var i = messages.length - 1; i >= 0; i--) {
    var attachments = messages[i].getAttachments();
    for (var j = 0; j < attachments.length; j++) {
      var name = attachments[j].getName().toLowerCase();
      if (name.indexOf(".xls") !== -1) {
        attachment = attachments[j];
        message = messages[i];
        break;
      }
    }
    if (attachment) break;
  }

  if (!attachment) {
    notify("Found email(s) but no .xls attachment.");
    return;
  }

  Logger.log("Processing attachment: " + attachment.getName() +
             " from email dated " + message.getDate());

  // 2. Upload & convert .xls to a temporary Google Sheet
  var tempFileId = convertXlsToSheet_(attachment);
  if (!tempFileId) {
    notify("Failed to convert the .xls attachment.");
    return;
  }

  try {
    // 3. Read data from the converted temp sheet
    var tempSS = SpreadsheetApp.openById(tempFileId);
    var tempSheet = tempSS.getSheets()[0]; // first sheet
    var tempData = tempSheet.getDataRange().getValues();

    if (tempData.length < DATA_START_ROW) {
      notify("The .xls file appears to have too few rows (" + tempData.length + ").");
      return;
    }

    // 4. Write data into the destination sheet (rows 3 onward, 1-indexed)
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    if (!ss) {
      ss = SpreadsheetApp.openById(SpreadsheetApp.getActiveSpreadsheet().getId());
    }
    var destSheet = ss.getSheetByName(SHEET_NAME);
    if (!destSheet) {
      notify('Sheet "' + SHEET_NAME + '" not found in this spreadsheet.');
      return;
    }

    // We copy from row index (DATA_START_ROW - 1) in the temp data to the end
    var sourceStartIndex = DATA_START_ROW - 1; // 0-indexed
    var rowsToCopy = tempData.length - sourceStartIndex;
    var colsToCopy = tempData[sourceStartIndex].length;

    // Ensure the destination sheet has enough rows and columns
    if (destSheet.getMaxRows() < DATA_START_ROW + rowsToCopy - 1) {
      destSheet.insertRowsAfter(destSheet.getMaxRows(),
        (DATA_START_ROW + rowsToCopy - 1) - destSheet.getMaxRows());
    }
    if (destSheet.getMaxColumns() < colsToCopy) {
      destSheet.insertColumnsAfter(destSheet.getMaxColumns(),
        colsToCopy - destSheet.getMaxColumns());
    }

    // Build the data slice to write (from row 3 onward in the source)
    var dataSlice = tempData.slice(sourceStartIndex);

    // Normalize: ensure every row has the same number of columns
    for (var r = 0; r < dataSlice.length; r++) {
      while (dataSlice[r].length < colsToCopy) {
        dataSlice[r].push("");
      }
    }

    // Write values into destination (starting at row DATA_START_ROW, column 1)
    var destRange = destSheet.getRange(DATA_START_ROW, 1, rowsToCopy, colsToCopy);
    destRange.setValues(dataSlice);

    Logger.log("Updated " + rowsToCopy + " rows x " + colsToCopy +
               " cols starting at row " + DATA_START_ROW);

    // 5. Label the email as processed
    labelEmailProcessed_(thread);

    notify("Import complete!\n\n" +
           "Source: " + attachment.getName() + "\n" +
           "Date: " + message.getDate() + "\n" +
           "Rows updated: " + rowsToCopy);

  } finally {
    // 6. Clean up: delete the temporary converted file
    try {
      DriveApp.getFileById(tempFileId).setTrashed(true);
      Logger.log("Temp file deleted: " + tempFileId);
    } catch (e) {
      Logger.log("Could not delete temp file: " + e.message);
    }
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────────

/**
 * Converts an .xls attachment to a Google Sheet using the Drive API v3
 * via UrlFetchApp. No advanced service needed.
 * Returns the file ID of the new Google Sheet, or null on failure.
 */
function convertXlsToSheet_(attachment) {
  try {
    var blob = attachment.copyBlob();
    var name = attachment.getName().toLowerCase();
    if (name.indexOf(".xlsx") !== -1) {
      blob.setContentType("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
    } else {
      blob.setContentType("application/vnd.ms-excel");
    }

    // Use Drive API v3 via UrlFetchApp to upload and convert
    var metadata = {
      name: "Golf Pool Import (TEMP) - " + new Date().toISOString(),
      mimeType: "application/vnd.google-apps.spreadsheet"
    };

    var boundary = "-------golf_pool_boundary";
    var requestBody =
      "--" + boundary + "\r\n" +
      "Content-Type: application/json; charset=UTF-8\r\n\r\n" +
      JSON.stringify(metadata) + "\r\n" +
      "--" + boundary + "\r\n" +
      "Content-Type: " + blob.getContentType() + "\r\n" +
      "Content-Transfer-Encoding: base64\r\n\r\n" +
      Utilities.base64Encode(blob.getBytes()) + "\r\n" +
      "--" + boundary + "--";

    var options = {
      method: "post",
      contentType: "multipart/related; boundary=" + boundary,
      payload: requestBody,
      headers: {
        Authorization: "Bearer " + ScriptApp.getOAuthToken()
      },
      muteHttpExceptions: true
    };

    var url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart";
    var response = UrlFetchApp.fetch(url, options);
    var result = JSON.parse(response.getContentText());

    if (result.error) {
      Logger.log("Drive API error: " + JSON.stringify(result.error));
      return null;
    }

    Logger.log("Converted .xls to Google Sheet: " + result.id);
    return result.id;

  } catch (e) {
    Logger.log("convertXlsToSheet_ error: " + e.message);
    return null;
  }
}

/**
 * Applies the "Golf-Processed" label to a Gmail thread.
 * Creates the label if it doesn't exist.
 */
function labelEmailProcessed_(thread) {
  var label;
  try {
    label = GmailApp.getUserLabelByName(GMAIL_LABEL);
    if (!label) {
      label = GmailApp.createLabel(GMAIL_LABEL);
    }
    thread.addLabel(label);
    Logger.log("Labeled thread as " + GMAIL_LABEL);
  } catch (e) {
    Logger.log("Could not label thread: " + e.message);
  }
}

// ── Debug / Test ───────────────────────────────────────────────────────────────

/**
 * Test the .xls conversion in isolation. Run this from the editor
 * and check the Execution log for results.
 */
function testConvert() {
  var query = "from:" + SENDER_EMAIL + " has:attachment";
  var threads = GmailApp.search(query, 0, 1);
  if (threads.length === 0) {
    Logger.log("No threads found");
    return;
  }
  var msgs = threads[0].getMessages();
  var attachment = null;

  for (var i = msgs.length - 1; i >= 0; i--) {
    var atts = msgs[i].getAttachments();
    for (var j = 0; j < atts.length; j++) {
      if (atts[j].getName().toLowerCase().indexOf(".xls") !== -1) {
        attachment = atts[j];
        break;
      }
    }
    if (attachment) break;
  }

  if (!attachment) {
    Logger.log("No .xls attachment found");
    return;
  }

  Logger.log("Attachment: " + attachment.getName());
  Logger.log("Content type: " + attachment.getContentType());
  Logger.log("Size: " + attachment.getSize());

  var fileId = convertXlsToSheet_(attachment);
  if (fileId) {
    Logger.log("SUCCESS - File ID: " + fileId);
    DriveApp.getFileById(fileId).setTrashed(true);
    Logger.log("Temp file deleted");
  } else {
    Logger.log("FAILED - convertXlsToSheet_ returned null");
  }
}
