import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import App from "./App";

beforeEach(() => {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }));
  // 強制 mock 模式且加速(mockStep=5ms),避免打真後端、避免測試過慢
  window.history.replaceState({}, "", "/?mock=1&mockStep=5");
});

test("送出 prompt 後,mock 流最終跑到完成:四道閘全綠 + 可下載", async () => {
  const { container } = render(<App />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "樹與二元樹" } });
  fireEvent.click(screen.getByRole("button", { name: /鍛造/ }));

  // 大綱出現
  await waitFor(() => expect(screen.getByText("樹與二元樹")).toBeInTheDocument());
  // 最終完成:下載連結出現
  await waitFor(() => expect(screen.getByRole("link", { name: /下載/ })).toBeInTheDocument(), { timeout: 4000 });
  // 四道閘皆 pass
  expect(container.querySelectorAll('[data-gate][data-status="pass"]')).toHaveLength(4);
});
