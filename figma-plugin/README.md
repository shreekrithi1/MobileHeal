# MobileHeal Design Sync (Figma plugin) — live two-way, any Figma plan

1. Figma desktop → Plugins → Development → **Import plugin from manifest…** → pick `figma-plugin/manifest.json`.
2. Open your design file and run **MobileHeal Design Sync**. Keep it open while you work.

With **Live two-way sync** on:
- **MobileHeal → Figma:** every 5 s the plugin checks the design approved in MobileHeal and applies it to the
  “MobileHeal” variables and every frame named “Profile” (button colour/label, banner colour, required `*` markers).
- **Figma → MobileHeal:** when you edit those frames, the plugin sends the new values to MobileHeal. They become one
  “From Figma” change request that waits for a UX designer or portal admin, then joins the pipeline.

Layer naming: the button layer contains “Button”, the banner “Banner”, inputs “Input / Email” with a text label
(“Email *” = required). Free/Professional plans work — no Enterprise Variables API needed.
