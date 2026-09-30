# Osumtech Design Rules

## 1. Brand identity
- Brand name: Osumtech.
- Tagline: Good technology. Great possibilities.
- Direction: warm, confident, technically capable and approachable.
- Use cream, charcoal and purposeful orange accents.
- Avoid corporate blue, purple gradients, neon effects and generic
  technology styling.

## 2. Colour tokens
Use shared CSS variables or equivalent theme tokens, not scattered hex values.

Light theme:
--osum-brand: #FF553C;
--osum-bg: #F6F3EC;
--osum-surface: #FFFDF8;
--osum-soft: #ECE6DA;
--osum-text: #141210;
--osum-muted: #5F594F;
--osum-border: #DCD4C6;
--osum-action: #CE3E25;
--osum-on-action: #FFFFFF;

Dark theme:
--osum-brand: #FF553C;
--osum-bg: #0C0B0A;
--osum-surface: #141210;
--osum-soft: #1A1714;
--osum-text: #F4F0E8;
--osum-muted: #A09889;
--osum-border: #3C352E;
--osum-action: #FF6B42;
--osum-on-action: #17110D;

Brand orange is for artwork and decorative emphasis.
Action orange is for buttons, links and small orange text.
Never recolour the logo to match a button.
Keep most surfaces neutral. Use status colours only for real statuses,
with text labels or icons.

## 3. Typography
- Inter: body, navigation, dashboards, forms, tables and invoices.
- Instrument Serif: selected editorial/marketing display headings.
- JetBrains Mono: occasional technical metadata.
- Never recreate the wordmark by typing it.
- Fallbacks: system sans-serif, Georgia and system monospace.
- Body/input text: 16–18px.
- Regular labels and controls: at least 14px.
- Secondary metadata: 12–13px.
- Dashboard titles: normally 28–40px.
- Use tabular numerals for comparable numbers.
- Avoid serif fonts in dense operational screens.

## 4. Logo and the signature cropped O
- Reuse approved logo and original tilted-O assets.
- Inspect existing project assets before choosing files.
- If assets are missing, ask for them. Do not draw a replacement ring,
  type an O, auto-trace it or invent a new logo.
- Preserve proportions, tilt, inner opening and spacing.
- Light surfaces: orange O with charcoal wordmark.
- Dark surfaces: orange O with white wordmark.
- Do not invert the entire logo with CSS.
- Do not place an extra O beside a full logo.

For a cropped background motif:
- Use the actual tilted-O artwork behind content.
- Position at an edge/corner with about 35–55% outside the container.
- Typical size: 25–45% of container width.
- Starting opacity: 10–16% on light, 14–20% on dark.
- Keep its opening recognisable and important content unobstructed.
- Clip the decoration locally; do not clip interactive content.
- Make it noninteractive and hidden from assistive technology.
- Use selectively, not on every card or beside every heading.

## 5. Layout and components
- Use spacing steps of 8, 16, 24, 32, 48 and 64px.
- Allow 4px adjustments for small internal spacing.
- Cards/panels: 8–12px radius, subtle border, minimal shadow.
- Buttons/inputs: 6–8px radius.
- Reserve pills for short badges and filters.
- Use clear alignment and meaningful whitespace.
- Avoid wrapping every section in an identical card.
- One prominent primary action per local task.
- Use specific button labels such as Save changes or Import CDR.
- Reuse shared components and existing framework conventions.

## 6. Dashboards and data
- Prioritise scanning, filtering and completing tasks.
- Use compact Inter typography with readable labels.
- Right-align money and comparable numeric columns.
- Show units and currencies explicitly.
- Use restrained table dividers and clear status labels.
- Provide real loading, empty, error and success states.
- Never fabricate activity, statistics or records.
- Keep decoration away from dense tables and charts.
- Preserve existing calculations, filters and business behaviour.

## 7. Forms and accessibility
- Persistent labels; placeholders must not replace labels.
- Clear validation messages; retain entered data after failure.
- Visible keyboard focus and keyboard-operable controls.
- Aim for at least 44px touch targets.
- Normal text contrast target: at least 4.5:1.
- Verify actual rendered colour pairs.
- Do not communicate status through colour alone.
- Honour reduced-motion preferences.

## 8. Responsive behaviour
- Start at 360–390px and also verify 320px, tablet and desktop.
- Phone side padding: normally 16–20px.
- Stack content in logical reading order.
- Keep essential actions accessible.
- Confine wide-table scrolling to the table container.
- No page-wide horizontal overflow.
- Keep content usable at 200% text enlargement.

## 9. Themes and print
- Light and dark modes share layout and component structure.
- Only implement additional themes when requested or already supported.
- Printed documents use white paper and charcoal text.
- Hide navigation and editing controls in print.
- Keep printed output understandable in grayscale.

## 10. Invoice-specific rules, when applicable
- Clear invoice number, dates, customer and currency.
- Default table: Description | Amount.
- Show quantity/unit price only when enabled.
- Hidden quantity must never conceal a multiplier affecting totals.
- Show tax/discount rows only when applicable.
- Avoid awkward page breaks in items, totals and payment details.
- Repeat table headers on multipage invoices.
- Keep PDF text selectable.
- Filename: Osumtech - Invoice INV-0003 - Customer Name.pdf.

## 11. Content
- Plain, confident English; British spelling for UK-facing content.
- No invented reviews, clients, statistics, locations or promises.
- Keep implementation details out of customer-facing copy.
- Use approved business and contact details.

## 12. Implementation and verification
- Inspect the existing code and assets before changing UI.
- Preserve functionality, records and calculations during styling work.
- Do not replace the stack or data model merely to apply the theme.
- Review mobile wrapping, contrast, logo proportions and overflow.
- Test affected interactions and inspect the rendered result.
- Explain any necessary deviation from these rules.
