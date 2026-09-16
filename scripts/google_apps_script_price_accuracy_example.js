/**
 * Example Google Apps Script (bound to the same spreadsheet as inertia price updates).
 *
 * 1. Deploy as Web App OR run from the editor after selecting a function (for manual runs).
 * 2. Set script properties: INERTIA_BASE (e.g. https://inertiainvest.in), PRICE_ACCURACY_TOKEN
 * 3. Sheet tab is written by the Hub "Push queue to Google Sheets" button (default: PriceAccuracyQueue).
 *
 * Adjust column indices if you change the headers in services/price_accuracy_sheets.py.
 */
const PROP_BASE = 'INERTIA_BASE'; // no trailing slash
const PROP_TOKEN = 'PRICE_ACCURACY_API_TOKEN';

function _base() {
  const p = PropertiesService.getScriptProperties();
  return (p.getProperty(PROP_BASE) || '').replace(/\/$/, '');
}
function _token() {
  return PropertiesService.getScriptProperties().getProperty(PROP_TOKEN) || '';
}

/**
 * Read queue tab: for each data row, if verified_ok column is "Y", POST finding id to ack.
 * If new_close_price is filled for a row with a date, POST a price fix.
 *
 * Header row (row 1): see Python _HEADERS in price_accuracy_sheets.py
 * security_id(1) symbol(2) finding_id(3) kind(4) date_prev(5) date_next(6) ...
 * missing_date(10) new_close_price(11) verified_ok(12)
 */
function processPriceAccuracyQueueFromSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ss.getSheetByName('PriceAccuracyQueue');
  if (!sh) throw new Error('Missing worksheet PriceAccuracyQueue');
  const vals = sh.getDataRange().getValues();
  if (vals.length < 2) return;
  const hdr = vals[0].map(String);
  const fixes = [];
  for (let r = 1; r < vals.length; r++) {
    const row = vals[r];
    const kind = String(row[3] || '').toUpperCase();
    const findingId = row[2];
    const securityId = row[0];
    const verified = String(row[11] || '').trim().toUpperCase();
    const newClose = row[10];
    if (verified === 'Y' && findingId) {
      UrlFetchApp.fetch(_base() + '/api/v1/price-accuracy/webhook/findings/verified-by-id', {
        method: 'post',
        contentType: 'application/json',
        headers: { 'X-Price-Accuracy-Token': _token() },
        payload: JSON.stringify({ finding_id: Number(findingId), source: 'google_sheets_webhook' }),
        muteHttpExceptions: true,
      });
    }
    if (newClose !== '' && newClose != null && !isNaN(Number(newClose))) {
      let d = '';
      if (kind === 'MISSING' || kind === 'ZERO') {
        d = formatYmd_(row[9]); // missing_date
      } else {
        // SPIKE: default to adjusting the later close (date_next column)
        d = formatYmd_(row[5]);
      }
      if (securityId && d) {
        fixes.push({ security_id: Number(securityId), date: d, close_price: Number(newClose) });
      }
    }
  }
  if (fixes.length) {
    const resp = UrlFetchApp.fetch(_base() + '/api/v1/price-accuracy/webhook/apply-prices', {
      method: 'post',
      contentType: 'application/json',
      headers: { 'X-Price-Accuracy-Token': _token() },
      payload: JSON.stringify({ fixes: fixes, source: 'google_sheets_webhook' }),
      muteHttpExceptions: true,
    });
    // Surface CA-adjusted rejections (GOOGLEFINANCE ≠ as-traded NSE history).
    try {
      const body = JSON.parse(resp.getContentText() || '{}');
      const rejected = Number(body.rejected_adjusted || 0);
      if (rejected > 0) {
        const msg =
          body.user_message ||
          'Need unadjusted NSE close (as-traded). Do not import GOOGLEFINANCE when a later corporate action exists.';
        SpreadsheetApp.getUi().alert(
          'Price apply: ' + rejected + ' row(s) rejected\n\n' + msg
        );
      }
    } catch (e) {
      // ignore parse errors
    }
  }
}

function formatYmd_(v) {
  if (v instanceof Date) {
    return Utilities.formatDate(v, Session.getScriptTimeZone(), 'yyyy-MM-dd');
  }
  const s = String(v || '').slice(0, 10);
  return s;
}
