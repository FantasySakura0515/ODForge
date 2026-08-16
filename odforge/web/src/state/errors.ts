// 把後端錯誤的 stage 代碼翻成一句白話前綴——不把原始技術訊息當標題轟人。
// 原始 message 由 UI 收進 <details>「技術細節」。
const STAGE_ZH: Record<string, string> = {
  outline: "AI 構思大綱時出錯",
  slides: "AI 填充內容時出錯",
  render: "引擎渲染時出錯",
  validate: "驗證關卡未通過",
  preview: "產生預覽時出錯",
  qa: "設計 QA 時出錯",
  connect: "無法連上後端",
  // 大綱確認站等太久(後端 30 分鐘逾時)——不是系統壞掉,是沒人按確認。
  outline_approval: "大綱確認逾時，請重新生成",
  // 使用者主動取消(後端以 error 事件收尾,stage="cancel")。
  cancel: "已取消這次生成",
  // 後端有回應但拒絕請求(如 429 同時工作數上限、422 驗證失敗)——不是連不上。
  request: "後端拒絕了這次請求",
};

export function humanizeStage(stage?: string): string {
  return (stage && STAGE_ZH[stage]) || "發生錯誤";
}
