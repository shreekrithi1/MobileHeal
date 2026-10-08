# CR-2: Remove Zip Code

Requested by: You  
Created: 2026-10-08T04:39:28+00:00

## Requirement

Remove Zip Code

## UX changes

- Remove rule for **Zip** (`zip`)

## Design notes

- “Zip” is no longer a rule; the app stops prompting for it (stored values are kept).

## Accessibility

| Element | Colours | Ratio | WCAG |
|---|---|---|---|
| Save button | #FFFFFF on #6750A4 | 6.44:1 | AA |
| Alert banner | #B54708 on #FFF4E5 | 4.99:1 | AA |

## User impact

0 of 1 existing profiles will be prompted; 1 alerts will clear.
