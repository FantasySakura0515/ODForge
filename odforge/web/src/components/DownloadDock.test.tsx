import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { DownloadDock } from "./DownloadDock";

test("未就緒(生成中)顯示「尚未生成」且不可下載", () => {
  render(<DownloadDock jobId={undefined} downloadUrl={undefined} docType="odp" phase="generating" />);
  const btn = screen.getByRole("button");
  expect(btn).toHaveTextContent(/尚未生成/);
  expect(btn).toBeDisabled();
  expect(screen.queryByRole("link")).toBeNull();
});

test("空台(phase empty)整顆隱藏,不留「生成中…」矛盾殘影", () => {
  const { container } = render(<DownloadDock jobId={undefined} downloadUrl={undefined} docType="odp" phase="empty" />);
  expect(container.firstChild).toBeNull();
});

test("完成時為連結、副檔名依型別", () => {
  render(<DownloadDock jobId="j1" downloadUrl="/api/jobs/j1/download" docType="ods" phase="complete" />);
  const link = screen.getByRole("link");
  expect(link).toHaveAttribute("href", "/api/jobs/j1/download");
  expect(link).toHaveTextContent(/\.ods/);
});

test("展示模式(jobId=mock)不出真下載連結,改 disabled 誠實鈕(避免 /api/jobs/mock/download 404)", () => {
  render(<DownloadDock jobId="mock" downloadUrl="mock:download" docType="odp" phase="complete" />);
  expect(screen.queryByRole("link")).toBeNull();
  const btn = screen.getByRole("button");
  expect(btn).toBeDisabled();
  expect(btn).toHaveTextContent(/不提供下載/);
});
