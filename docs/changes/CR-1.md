# CR-1: Zip Code is required

Requested by: You  
Created: 2026-10-08T04:37:56+00:00

## Requirement

Zip Code is required

## UX changes

- Add required field **Zip** (`zip`)

## Design notes

- New required input “Zip” is added below existing fields with a text keyboard. Users missing it see the alert banner and the field is highlighted in red until filled.

## Accessibility

| Element | Colours | Ratio | WCAG |
|---|---|---|---|
| Save button | #FFFFFF on #6750A4 | 6.44:1 | AA |
| Alert banner | #B54708 on #FFF4E5 | 4.99:1 | AA |

## User impact

1 of 1 existing profiles will be prompted; 0 alerts will clear.
