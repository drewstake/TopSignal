// Compare committed blobs. Only reviewed presentation slots can differ.
const { execFileSync } = require("node:child_process");
const { createRequire } = require("node:module");
const requireFrontend = createRequire(__filename === "[stdin]" ?
  require("node:path").resolve("frontend/package.json") : require.resolve("../frontend/package.json"));
const ts = requireFrontend("typescript");
const postcss = requireFrontend("postcss");
const git = (...args) => execFileSync("git", args, { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
const displayTags = new Set("div span p h1 h2 h3 h4 section article header footer aside li ul ol dl dt dd table tr td th caption title desc svg circle path g".split(" "));
const displayFiles = new Set([
  ...["DonutRing", "GaugeBar", "MetricCard", "MiniStatList", "SplitBar"].map(n => "frontend/src/components/metrics/" + n + ".tsx"),
  ...["CompactDashboardPerformance", "DashboardOverview", "RiskForecastChart", "DailyAccountBalanceCard"].map(n => "frontend/src/pages/dashboard/components/" + n + ".tsx"),
  "frontend/src/pages/bot/BotChartPresentation.tsx",
  "frontend/src/pages/bot/TopBotEvaluation.tsx",
  "frontend/src/pages/bot/TopBotTradeRecorder.tsx",
  "frontend/src/pages/recorders/CaptureHealth.tsx",
]);
const properties = new Set("color background-color border-color border-width border-radius font-size font-weight font-family line-height letter-spacing padding padding-top padding-bottom padding-left padding-right margin margin-top margin-bottom margin-left margin-right gap row-gap column-gap width height min-width max-width min-height max-height grid-template-columns grid-template-rows align-items justify-content flex-direction flex-wrap fill stroke stroke-width".split(" "));

function cssShape(source) {
  const css = postcss.parse(source);
  css.walkDecls(decl => {
    // Keep rules, selectors, at-rules and all non-display properties verbatim.
    if (properties.has(decl.prop) && /^[\w #.,()%+/-]+$/.test(decl.value) &&
        !/(?:url|image|attr|expression)\s*\(/i.test(decl.value)) {
      decl.value = "DISPLAY";
      delete decl.raws.value;
    }
  });
  return css.toString();
}

function jsxShape(source) {
  const file = ts.createSourceFile("display.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  if (file.parseDiagnostics.length) throw Error("invalid TSX");
  const slots = [];
  function visit(node) {
    if (ts.isJsxText(node) && ts.isJsxElement(node.parent) &&
        displayTags.has(node.parent.openingElement.tagName.getText(file))) {
      slots.push([node.pos, node.end, "#text"]);
    }
    if (ts.isJsxAttribute(node) && node.name.getText(file) === "className" &&
        displayTags.has(node.parent.parent.tagName.getText(file)) &&
        node.initializer && ts.isStringLiteral(node.initializer) &&
        /^[\w :./%\[\]-]+$/.test(node.initializer.text)) {
      slots.push([node.initializer.getStart(file), node.initializer.end, '"#class"']);
    }
    if (ts.isJsxAttribute(node) && ["fill","stroke","strokeWidth"].includes(node.name.getText(file)) &&
        ["circle","line","path","rect"].includes(node.parent.parent.tagName.getText(file)) &&
        node.initializer && ts.isStringLiteral(node.initializer) &&
        /^(?:#[0-9a-f]{3,8}|currentColor|none|\d+(?:\.\d+)?|rgb\(var\(--theme-[a-z-]+\)\))$/i.test(node.initializer.text)) {
      slots.push([node.initializer.getStart(file), node.initializer.end, '"#paint"']);
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
  for (const [start, end, replacement] of slots.reverse()) {
    source = source.slice(0, start) + replacement + source.slice(end);
  }
  return source;
}

function classify(backend, dashboard, commit) {
  let count = 0;
  for (const base of new Set([backend, dashboard])) {
    const fields = git("diff", "--no-renames", "--raw", "-z", base, commit).split("\0");
    for (let i = 0; i + 1 < fields.length; i += 2) {
      const [record, name] = fields.slice(i, i + 2);
      let shape;
      if (/^frontend\/src\/(?:styles|pages|components)\/[\w/-]+\.css$/.test(name)) shape = cssShape;
      if (displayFiles.has(name)) shape = jsxShape;
      if (!shape || !/^:100644 100644 [0-9a-f]+ [0-9a-f]+ M$/.test(record)) {
        return { lane: "full", reason: name + " is outside reviewed display slots" };
      }
      if (shape(git("show", base + ":" + name)) !== shape(git("show", commit + ":" + name))) {
        return { lane: "full", reason: name + " changes structure, code or a non-display value" };
      }
      count++;
    }
  }
  return count ? { lane: "dashboard", reason: "only reviewed display slots differ from both peteamdpc revisions" }
    : { lane: "full", reason: "no presentation delta" };
}

if (require.main === module || __filename === "[stdin]") {
  try { console.log(JSON.stringify(classify(...process.argv.slice(2)))); }
  catch (error) { console.log(JSON.stringify({ lane: "full", reason: "comparison failed: " + error.message })); }
}
module.exports = { classify, cssShape, jsxShape };
