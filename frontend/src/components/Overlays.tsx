import { lazy, Suspense, useEffect } from "react";
import { closePalette, closeStockSheet, togglePalette, usePaletteOpen, useStockSheet } from "../ui/overlays";

/*
 * ⌘K 빠른 이동과 종목 빠른 보기 시트 — 처음 열 때 그 조각을 받는다 (첫 화면 코드에는 들어가지 않는다)
 */
const CommandPalette = lazy(() => import("./CommandPalette").then((m) => ({ default: m.CommandPalette })));
const StockSheet = lazy(() => import("./StockSheet").then((m) => ({ default: m.StockSheet })));

/** ⌘K / Ctrl+K 로 열고 닫는다. 휴대폰은 상단 바의 🔍 버튼 */
export function CommandPaletteHost() {
  const open = usePaletteOpen();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        togglePalette();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => () => closePalette(), []);
  if (!open) return null;
  return (
    <Suspense fallback={null}>
      <CommandPalette onClose={closePalette} />
    </Suspense>
  );
}

/** 보유·관심 종목 줄을 휴대폰에서 누르면 뜨는 종목 빠른 보기 */
export function StockSheetHost() {
  const target = useStockSheet();
  useEffect(() => () => closeStockSheet(), []);
  if (!target) return null;
  return (
    <Suspense fallback={null}>
      <StockSheet key={`${target.market}:${target.symbol}`} target={target} onClose={closeStockSheet} />
    </Suspense>
  );
}
