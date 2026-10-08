# CR-6: Make the save button green and call it “Save changes”

Requested by: You  
Created: 2026-10-08T05:38:51+00:00

## Requirement

Make the save button green and call it “Save changes”

## UX changes

- **Button Color**: default (#6750A4) → #079455
- **Button Label**: default (Save) → Save changes

## Design notes

- Colour changes are applied live over the WebSocket; no app release is required.
- ⚠ Save button contrast is 3.91:1 (AA Large). WCAG AA needs 4.5:1 for normal text — consider a darker/lighter text colour.

## Accessibility

| Element | Colours | Ratio | WCAG |
|---|---|---|---|
| Save button | #FFFFFF on #079455 | 3.91:1 | AA Large |
| Alert banner | #B54708 on #FFF4E5 | 4.99:1 | AA |

## User impact

0 of 2 existing profiles will be prompted; 0 alerts will clear.
