# Apps Script change: store the consent record

The sign-up form posts to a Google Apps Script. That script is not in this repository.
Ahmed must make this change in the Apps Script editor.

## Why

The form now has an unticked box: "Send me the Daily Edge AI email. I can unsubscribe at any time."
The browser and the page script stop a submit until the box is ticked.
The privacy page says that a consent record is kept: the date, the time and the words.
The script must store that record with each email.

## What the site sends

`site/js/subscribe.js` sends a `text/plain` POST. The body is JSON:

```json
{"email": "name@example.com"}
```

The site expects a reply that starts with `Success!`. Do not change the reply.

## The change

1. Open the Google Sheet that holds the emails.
2. Add 2 column headers after the email column: `consent_at` and `consent_text`.
3. Open Extensions > Apps Script.
4. Replace `doPost` with the code below. Keep your sheet name in `SHEET_NAME`.
5. Select Deploy > Manage deployments > Edit > Version: New version > Deploy. The URL stays the same.

```javascript
const SHEET_NAME = 'Sheet1'; // the tab that holds the emails
// The same words as CONSENT_TEXT in generate_html.py. Change both together.
const CONSENT_TEXT = 'Send me the Daily Edge AI email. I can unsubscribe at any time.';

function doPost(e) {
  const data = JSON.parse(e.postData.contents);
  const email = String(data.email || '').trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return ContentService.createTextOutput('Error: invalid email');
  }
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  // Columns: email, consent_at (UTC, ISO 8601), consent_text
  sheet.appendRow([email, new Date().toISOString(), CONSENT_TEXT]);
  return ContentService.createTextOutput('Success! Email captured: ' + email);
}
```

## Check

1. Open the site. Do not tick the box. Select Subscribe. The browser shows a message and sends nothing.
2. Tick the box. Enter a test email. Select Subscribe. The button shows "Subscribed".
3. Open the Sheet. The new row has the email, a time and the consent text.

## Limits

- The script writes the consent text itself. The site sends the email only, because `site/js/subscribe.js`
  was outside the scope of this change. The form cannot be sent without the tick, so each new row is a row with consent.
- Rows from before this change have no consent record. Decide what to do with them before the launch:
  ask those subscribers to sign up again, or delete the rows.
- When a subscriber unsubscribes, delete the whole row within 7 days. The privacy page says this.
