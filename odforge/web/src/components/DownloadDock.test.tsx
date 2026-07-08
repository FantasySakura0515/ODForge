import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { DownloadDock } from "./DownloadDock";

test("未完成時顯示生成中且不可下載", () => {
  render(<DownloadDock jobId={undefined} downloadUrl={undefined} docType="odp" />);
  expect(screen.getByText(/生成中/)).toBeInTheDocument();
  expect(screen.queryByRole("link")).toBeNull();
});

test("完成時為連結、副檔名依型別", () => {
  render(<DownloadDock jobId="j1" downloadUrl="/api/jobs/j1/download" docType="ods" />);
  const link = screen.getByRole("link");
  expect(link).toHaveAttribute("href", "/api/jobs/j1/download");
  expect(link).toHaveTextContent(/\.ods/);
});
