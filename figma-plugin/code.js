// MobileHeal Design Sync — applies the design approved in MobileHeal to this Figma file.
// 1. Variables: collection "MobileHeal" (ui/<key> COLOR|STRING, fields/<name>/required BOOLEAN).
// 2. Frames: the selected frame(s) or every frame named "Profile": button fill + label, banner fill,
//    required markers (" *") on "Input / <Field>" labels.
figma.showUI(__html__, { width: 320, height: 330 });

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return { r: parseInt(h.slice(0, 2), 16) / 255, g: parseInt(h.slice(2, 4), 16) / 255, b: parseInt(h.slice(4, 6), 16) / 255 };
}

async function applyVariables(items) {
  const cols = await figma.variables.getLocalVariableCollectionsAsync();
  let col = cols.find((c) => c.name === "MobileHeal") || figma.variables.createVariableCollection("MobileHeal");
  const mode = col.modes[0].modeId;
  const existing = {};
  for (const id of col.variableIds) {
    const v = await figma.variables.getVariableByIdAsync(id);
    if (v) existing[v.name] = v;
  }
  for (const it of items) {
    let v = existing[it.name];
    if (v && v.resolvedType !== it.type) { v.remove(); v = null; }
    if (!v) v = figma.variables.createVariable(it.name, col, it.type);
    v.setValueForMode(mode, it.value);
  }
  return items.length;
}

async function setText(node, text) {
  if (node.type !== "TEXT" || node.characters === text) return false;
  const fonts = node.getRangeAllFontNames(0, node.characters.length);
  for (const f of fonts) await figma.loadFontAsync(f);
  node.characters = text;
  return true;
}

async function applyFrames(tokens) {
  let frames = figma.currentPage.selection.filter((n) => "findAll" in n);
  if (!frames.length) frames = await profileFrames();
  let changed = 0;
  for (const frame of frames) {
    for (const node of frame.findAll((n) => "name" in n)) {
      const name = node.name.toLowerCase();
      if (/\b(button|btn|cta|primary)\b/.test(name) && "fills" in node) {
        if (tokens["ui.button_color"]) { node.fills = [{ type: "SOLID", color: hexToRgb(tokens["ui.button_color"]) }]; changed++; }
        const label = node.findOne && node.findOne((n) => n.type === "TEXT");
        if (label && tokens["ui.button_label"] && (await setText(label, tokens["ui.button_label"]))) changed++;
      } else if (/\b(banner|alert|callout)\b/.test(name) && "fills" in node && tokens["ui.banner_color"]) {
        node.fills = [{ type: "SOLID", color: hexToRgb(tokens["ui.banner_color"]) }]; changed++;
      } else if (/\b(input|field|text ?field)\b/.test(name) && node.findOne) {
        const label = node.findOne((n) => n.type === "TEXT");
        if (!label) continue;
        const field = label.characters.replace(/\*/g, "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_");
        const rule = tokens[field] || tokens[{ phone: "phone_number", mobile: "phone_number", full_name: "name" }[field]];
        if (!rule) continue;
        const want = label.characters.replace(/\s*\*$/, "") + (rule === "required" ? " *" : "");
        if (await setText(label, want)) changed++;
      }
    }
  }
  return { frames: frames.length, changed };
}

// ---- Figma → MobileHeal: read the design tokens back out of the frame(s)
function rgbToHex(c) {
  const h = (x) => Math.round(x * 255).toString(16).padStart(2, "0").toUpperCase();
  return "#" + h(c.r) + h(c.g) + h(c.b);
}
async function profileFrames() {
  await figma.loadAllPagesAsync();
  return figma.root.findAll((n) => n.type === "FRAME" && /profile/i.test(n.name));
}
async function readTokens() {
  const tokens = {};
  for (const frame of await profileFrames()) {
    for (const node of frame.findAll((n) => "name" in n)) {
      const name = node.name.toLowerCase();
      const fill = "fills" in node && Array.isArray(node.fills) && node.fills.find((f) => f.type === "SOLID");
      if (/\b(button|btn|cta|primary)\b/.test(name)) {
        if (fill) tokens["ui.button_color"] = rgbToHex(fill.color);
        const label = node.findOne && node.findOne((n) => n.type === "TEXT");
        if (label) tokens["ui.button_label"] = label.characters.trim();
      } else if (/\b(banner|alert|callout)\b/.test(name) && fill) {
        tokens["ui.banner_color"] = rgbToHex(fill.color);
      } else if (/\b(input|field|text ?field)\b/.test(name) && node.findOne) {
        const label = node.findOne((n) => n.type === "TEXT");
        if (!label) continue;
        let field = label.characters.replace(/\*/g, "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
        field = { phone: "phone_number", mobile: "phone_number", full_name: "name" }[field] || field;
        if (field) tokens[field] = /\*\s*$/.test(label.characters) ? "required" : "optional";
      }
    }
  }
  return tokens;
}
let applying = false, timer = null;
figma.on("documentchange", () => {
  if (applying) return;                       // our own writes are not designer edits
  clearTimeout(timer);
  timer = setTimeout(async () => {
    figma.ui.postMessage({ type: "edited", tokens: await readTokens(), user: (figma.currentUser || {}).name || "a designer", file: figma.root.name });
  }, 2500);
});

figma.ui.onmessage = async (msg) => {
  if (msg.type === "read") {
    figma.ui.postMessage({ type: "edited", tokens: await readTokens(), user: (figma.currentUser || {}).name || "a designer", file: figma.root.name, manual: true });
    return;
  }
  if (msg.type !== "apply") return;
  applying = true;
  try {
    const n = await applyVariables(msg.data.variables);
    let text = `Updated ${n} variables in “MobileHeal”.`;
    if (!msg.onlyVars) {
      const r = await applyFrames(msg.data.tokens);
      text += ` ${r.changed} layer change(s) across ${r.frames} frame(s).`;
    }
    figma.notify("MobileHeal design applied");
    figma.ui.postMessage({ text: text });
  } catch (e) {
    figma.ui.postMessage({ text: "Failed: " + e.message, error: true });
  }
  setTimeout(() => { applying = false; }, 3000);
};
