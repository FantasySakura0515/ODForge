import { useEffect, useRef, useState } from "react";
import {
  deleteTemplate,
  extractTemplateDesign,
  getTemplates,
  saveTemplate,
  type DeckTemplate,
  type DesignSpec,
  type StyleId,
  type StyleOption,
} from "../state/api";

// 白名單與後端 ir.FONT_WHITELIST 一致;送出不在名單內的字體會被 422 擋下,
// 所以這裡就只給得出來的四種。
const FONTS = ["Noto Sans TC", "Noto Serif TC", "微軟正黑體", "標楷體"];
const SCALES: { id: DesignSpec["scale"]; label: string; hint: string }[] = [
  { id: "compact", label: "緊湊", hint: "字小、放得多" },
  { id: "standard", label: "標準", hint: "多數場合" },
  { id: "display", label: "放大", hint: "大字上台" },
];

const SWATCHES: { key: keyof DesignSpec["palette"]; label: string; hint: string }[] = [
  { key: "bg", label: "背景", hint: "頁面底色" },
  { key: "surface", label: "色塊", hint: "卡片與色塊底" },
  { key: "text", label: "內文", hint: "主要文字" },
  { key: "muted", label: "次要", hint: "註解與出處" },
  { key: "accent", label: "強調", hint: "標題線與關鍵字" },
];

const MAX_TEMPLATE_BYTES = 12 * 1024 * 1024;

/** 版式的顯示名稱;清單還沒到就先顯示 id,不要顯示空白。 */
function styleLabel(styles: StyleOption[], id: StyleId): string {
  return styles.find((option) => option.id === id)?.label ?? id;
}

const BLANK: DesignSpec = {
  palette: {
    bg: "#FBF9F4",
    surface: "#EFEADD",
    text: "#1F2733",
    muted: "#5B6470",
    accent: "#1A4B8C",
  },
  fonts: { display: "Noto Sans TC", body: "Noto Sans TC" },
  scale: "standard",
};

/** WCAG 相對亮度 → 對比度。與後端 ir.contrast_ratio 同一條公式。 */
function luminance(hex: string): number {
  const value = hex.replace("#", "");
  const channels = [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)];
  const [lighter, darker] = x >= y ? [x, y] : [y, x];
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * 送出前先在本機算一次對比,列出過不了的組合。
 *
 * 後端的 Palette 驗證才是真正的守門員(同一組門檻),這裡只是把它提早到使用者
 * 還在調色的時候——「存了才被退件」與「調的當下就看到 3.1 < 4.5」是兩種體驗。
 */
export function contrastProblems(palette: DesignSpec["palette"]): string[] {
  const checks: [string, string, string, number][] = [
    ["內文", palette.text, palette.bg, 4.5],
    ["強調", palette.accent, palette.bg, 3],
    ["次要", palette.muted, palette.bg, 3],
    ["內文（色塊上）", palette.text, palette.surface, 4.5],
    ["強調（色塊上）", palette.accent, palette.surface, 3],
    ["次要（色塊上）", palette.muted, palette.surface, 3],
  ];
  return checks
    .filter(([, fg, bg, floor]) => contrast(fg, bg) < floor)
    .map(
      ([label, fg, bg, floor]) =>
        `${label}對比 ${contrast(fg, bg).toFixed(2)}，低於 ${floor}`,
    );
}

function fileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("無法讀取檔案"));
    reader.readAsDataURL(file);
  });
}

/**
 * 一張迷你封面。**版式決定構圖**,配色只決定顏色——所以這張圖必須跟著 style 換
 * 排法。畫廊裡十三張構圖一模一樣的縮圖,正是「這些其實都是同一個範本」的來源。
 */
function TemplatePreview({
  design,
  style,
}: {
  design: DesignSpec;
  style: StyleId;
}) {
  const { palette, fonts } = design;
  // keynote bleeds the accent to every edge, so its text flips to the page
  // colour — exactly what the renderer does.
  const bleed = style === "keynote";
  return (
    <span
      className="tpl-preview"
      data-style={style}
      aria-hidden="true"
      style={{
        background: bleed ? palette.accent : palette.bg,
        fontFamily: fonts.body,
      }}
    >
      {style === "report" && (
        <i className="tpl-rule" style={{ background: palette.accent }} />
      )}
      {style === "editorial" && (
        <s className="tpl-masthead" style={{ background: palette.accent }} />
      )}
      {style === "classic" && (
        <s className="tpl-dots" style={{ background: palette.accent }} />
      )}
      <b
        style={{
          color: bleed ? palette.bg : palette.text,
          fontFamily: fonts.display,
        }}
      >
        標題
      </b>
      <em style={{ color: bleed ? palette.surface : palette.muted }}>副標與說明</em>
      {style === "academic" && (
        <s className="tpl-underrule" style={{ background: palette.accent }} />
      )}
      {(style === "classic" || style === "report") && (
        <s className="tpl-card" style={{ background: palette.surface }} />
      )}
    </span>
  );
}

export function TemplateGallery() {
  const [templates, setTemplates] = useState<DeckTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [styles, setStyles] = useState<StyleOption[]>([]);
  const [editing, setEditing] = useState<DesignSpec>();
  const [style, setStyle] = useState<StyleId>("report");
  const [name, setName] = useState("");
  const [source, setSource] = useState<"custom" | "extracted">("custom");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const nameRef = useRef<HTMLInputElement>(null);

  async function reload() {
    setLoading(true);
    setError("");
    try {
      const report = await getTemplates();
      setTemplates(report.templates);
      setStyles(report.styles ?? []);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "無法讀取範本庫。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  // 開啟編輯器時把焦點送進名稱欄:鍵盤使用者不必從頁首 Tab 一路找過來。
  useEffect(() => {
    if (editing) nameRef.current?.focus();
  }, [editing]);

  const builtins = templates.filter((t) => t.builtin);
  const custom = templates.filter((t) => !t.builtin);

  function startFrom(template: DeckTemplate) {
    setEditing({ ...template.design, palette: { ...template.design.palette } });
    setStyle(template.style);
    setName(template.builtin ? `${template.name} 改版` : template.name);
    setSource("custom");
    setFormError("");
  }

  async function importTemplateFile(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    if (file.size > MAX_TEMPLATE_BYTES) {
      setFormError(`範本檔請小於 ${MAX_TEMPLATE_BYTES / 1024 / 1024} MiB。`);
      return;
    }
    setFormError("");
    try {
      const design = await extractTemplateDesign(await fileAsDataUrl(file));
      setEditing(design);
      setName(file.name.replace(/\.[^.]+$/, ""));
      setSource("extracted");
    } catch (exc) {
      setFormError(exc instanceof Error ? exc.message : "無法讀取這個範本檔。");
    }
  }

  async function submit() {
    if (!editing || !name.trim()) return;
    setSaving(true);
    setFormError("");
    try {
      await saveTemplate({ name: name.trim(), design: editing, style, source });
      setEditing(undefined);
      setName("");
      await reload();
    } catch (exc) {
      setFormError(exc instanceof Error ? exc.message : "範本儲存失敗。");
    } finally {
      setSaving(false);
    }
  }

  async function remove(template: DeckTemplate) {
    try {
      await deleteTemplate(template.id);
      await reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "範本刪除失敗。");
    }
  }

  const problems = editing ? contrastProblems(editing.palette) : [];

  return (
    <section className="template-gallery" aria-labelledby="template-title">
      <header className="archive-head">
        <div>
          <h2 id="template-title">範本庫</h2>
          <span>
            {loading
              ? "讀取中"
              : `內建 ${builtins.length} 套 · 自訂 ${custom.length} 套`}
          </span>
        </div>
        <div className="tpl-actions">
          <label className="tpl-import">
            匯入現有範本
            <input
              type="file"
              accept=".otp,.odp,.ott,.odt,application/vnd.oasis.opendocument.presentation,application/vnd.oasis.opendocument.presentation-template"
              data-testid="template-import"
              onChange={(e) => void importTemplateFile(e.target.files)}
            />
          </label>
          <button
            type="button"
            className="archive-refresh"
            onClick={() => {
              setEditing({ ...BLANK, palette: { ...BLANK.palette } });
              setStyle("report");
              setName("");
              setSource("custom");
              setFormError("");
            }}
          >
            ＋ 自訂配色
          </button>
        </div>
      </header>

      <p className="tpl-lead">
        生成時可在「視覺主題」選一套；留白則由 AI 依題目定調。
        匯入學校或公司的公版 .otp／.odp，會抽出它的配色與字體存成你的範本。
      </p>

      {error && (
        <div className="archive-state archive-error" role="alert">
          <p>{error}</p>
          <button type="button" className="text-action" onClick={() => void reload()}>
            重試
          </button>
        </div>
      )}

      {/* 匯入失敗時編輯器根本沒開,錯誤訊息若只長在編輯器裡就等於沒說:使用者
          選了檔案,畫面完全沒反應。 */}
      {formError && !editing && (
        <p className="tpl-error" role="alert">{formError}</p>
      )}

      {editing && (
        <div className="tpl-editor" role="group" aria-label="編輯範本">
          <div className="tpl-editor-main">
            <TemplatePreview design={editing} style={style} />
            <div className="tpl-fields">
              <label className="advfield text-setting">
                <span className="advlabel">範本名稱</span>
                <input
                  ref={nameRef}
                  type="text"
                  maxLength={40}
                  placeholder="例：系上公版 2026"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>

              {styles.length > 0 && (
                <div className="advfield">
                  <span className="advlabel" id="style-label">版式</span>
                  <div
                    className="stylelist"
                    role="radiogroup"
                    aria-labelledby="style-label"
                  >
                    {styles.map((option) => (
                      <label
                        key={option.id}
                        className={
                          style === option.id ? "style-option on" : "style-option"
                        }
                      >
                        <input
                          type="radio"
                          name="tpl-style"
                          checked={style === option.id}
                          onChange={() => setStyle(option.id)}
                        />
                        <span>
                          <b>{option.label}</b>
                          <small>{option.blurb}</small>
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              )}

              <div className="advfield">
                <span className="advlabel">配色</span>
                <div className="tpl-swatches">
                  {SWATCHES.map((swatch) => (
                    <label key={swatch.key} className="tpl-swatch">
                      <input
                        type="color"
                        aria-label={swatch.label}
                        value={editing.palette[swatch.key]}
                        onChange={(e) =>
                          setEditing({
                            ...editing,
                            palette: { ...editing.palette, [swatch.key]: e.target.value.toUpperCase() },
                          })
                        }
                      />
                      <span>
                        <b>{swatch.label}</b>
                        <small>{editing.palette[swatch.key].toUpperCase()}</small>
                      </span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="settings-grid even">
                <label className="advfield text-setting">
                  <span className="advlabel">標題字體</span>
                  <select
                    value={editing.fonts.display}
                    onChange={(e) =>
                      setEditing({
                        ...editing,
                        fonts: { ...editing.fonts, display: e.target.value },
                      })
                    }
                  >
                    {FONTS.map((font) => (
                      <option key={font} value={font}>{font}</option>
                    ))}
                  </select>
                </label>
                <label className="advfield text-setting">
                  <span className="advlabel">內文字體</span>
                  <select
                    value={editing.fonts.body}
                    onChange={(e) =>
                      setEditing({
                        ...editing,
                        fonts: { ...editing.fonts, body: e.target.value },
                      })
                    }
                  >
                    {FONTS.map((font) => (
                      <option key={font} value={font}>{font}</option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="advfield">
                <span className="advlabel" id="scale-label">字級密度</span>
                <div className="chipset" role="group" aria-labelledby="scale-label">
                  {SCALES.map((scale) => (
                    <button
                      key={scale.id}
                      type="button"
                      className={editing.scale === scale.id ? "chip on" : "chip"}
                      aria-pressed={editing.scale === scale.id}
                      onClick={() => setEditing({ ...editing, scale: scale.id })}
                    >
                      {scale.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* 對比不足時擋在存檔之前。後端本來就會退件,但在調色的當下說,比存完
              才看到一句英文錯誤有用得多。 */}
          {problems.length > 0 && (
            <ul className="tpl-problems" data-testid="tpl-contrast" role="alert">
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
              <li className="tpl-problems-note">
                這幾組在投影幕上會看不清楚，請調深或調淺後再儲存。
              </li>
            </ul>
          )}
          {formError && <p className="tpl-error" role="alert">{formError}</p>}

          <div className="tpl-editor-actions">
            <button
              type="button"
              className="forge compact"
              disabled={!name.trim() || problems.length > 0 || saving}
              onClick={() => void submit()}
            >
              <span>{saving ? "儲存中…" : "儲存範本"}</span>
              <span className="forge-arrow" aria-hidden="true">✓</span>
            </button>
            <button type="button" className="text-action" onClick={() => setEditing(undefined)}>
              取消
            </button>
          </div>
        </div>
      )}

      {custom.length > 0 && (
        <>
          <h3 className="tpl-section">自訂</h3>
          <div className="tpl-grid" data-testid="tpl-custom">
            {custom.map((template) => (
              <article className="tpl-card" key={template.id}>
                <TemplatePreview design={template.design} style={template.style} />
                <div className="tpl-meta">
                  <strong>{template.name}</strong>
                  <small>
                    {styleLabel(styles, template.style)}
                    {template.source === "extracted" ? " · 匯入自範本檔" : ""}
                  </small>
                </div>
                <div className="tpl-card-actions">
                  <button type="button" className="text-action" onClick={() => startFrom(template)}>
                    以此為底
                  </button>
                  <button
                    type="button"
                    className="text-action danger"
                    onClick={() => void remove(template)}
                    aria-label={`刪除範本 ${template.name}`}
                  >
                    刪除
                  </button>
                </div>
              </article>
            ))}
          </div>
        </>
      )}

      <h3 className="tpl-section">內建</h3>
      <div className="tpl-grid" data-testid="tpl-builtin">
        {builtins.map((template) => (
          <article className="tpl-card" key={template.id}>
            <TemplatePreview design={template.design} style={template.style} />
            <div className="tpl-meta">
              <strong>{template.name}</strong>
              <small>{styleLabel(styles, template.style)}</small>
            </div>
            <div className="tpl-card-actions">
              <button type="button" className="text-action" onClick={() => startFrom(template)}>
                以此為底
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
