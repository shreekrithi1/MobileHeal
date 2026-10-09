# MobileHeal Design Sync (Figma plugin)

Applies the design approved in MobileHeal to a Figma file: works on every Figma plan (the REST Variables API needs Enterprise).

1. Figma desktop → **Plugins → Development → Import plugin from manifest…** → choose `figma-plugin/manifest.json`.
2. Start MobileHeal (`./start.command`), open your design file, select the Profile frame.
3. Run **MobileHeal Design Sync → Apply approved design**.

It updates the **MobileHeal** variable collection (`ui/*` colours and labels, `fields/<name>/required`) and the frame:
button colour and label, banner colour, and the required marker (`*`) on `Input / <Field>` labels.
Then publish a version in Figma — MobileHeal sees the version and confirms nothing differs.
