import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { LineChart, BarChart, PieChart, HeatmapChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  VisualMapComponent,
  ToolboxComponent,
  GraphicComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsCoreOption } from "echarts/core";
echarts.use([
  LineChart,
  BarChart,
  PieChart,
  HeatmapChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  VisualMapComponent,
  ToolboxComponent,
  GraphicComponent,
  CanvasRenderer,
]);
export function Chart({
  option,
  onClick,
  height = 300,
  label,
}: {
  option: EChartsCoreOption;
  onClick?: (index: number, name: string) => void;
  height?: number;
  label: string;
}) {
  const [light, setLight] = useState(
    document.documentElement.dataset.theme === "light",
  );
  useEffect(() => {
    const observer = new MutationObserver(() =>
      setLight(document.documentElement.dataset.theme === "light"),
    );
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => observer.disconnect();
  }, []);
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const instance = echarts.init(ref.current);
    chart.current = instance;
    const obs = new ResizeObserver(() => instance.resize());
    obs.observe(ref.current);
    return () => {
      obs.disconnect();
      instance.dispose();
    };
  }, []);
  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    c.setOption(light ? adaptLight(option) : option, true);
    c.off("click");
    if (onClick) c.on("click", (p) => onClick(p.dataIndex, p.name));
  }, [option, onClick, light]);
  return (
    <div
      ref={ref}
      className="chart"
      style={{ height }}
      role="img"
      aria-label={label}
    />
  );
}
export const palette = [
  "#a9eed5",
  "#8aa9ff",
  "#e8be78",
  "#b69cec",
  "#6bc4d9",
  "#e79aa9",
  "#8696a8",
];
export const chartBase = {
  backgroundColor: "transparent",
  color: palette,
  textStyle: { fontFamily: "inherit", color: "#92a0b1", fontSize: 11 },
  tooltip: {
    trigger: "axis",
    backgroundColor: "#182330",
    borderColor: "#304154",
    textStyle: { color: "#edf3f8" },
    confine: true,
  },
  grid: { left: 12, right: 18, top: 24, bottom: 35, containLabel: true },
  toolbox: {
    show: true,
    right: 8,
    iconStyle: { borderColor: "#8193a9" },
    feature: {
      saveAsImage: {
        title: "Save chart",
        backgroundColor: "#101923",
        pixelRatio: 2,
      },
    },
  },
};

function adaptLight<T>(value: T): T {
  const colors: Record<string, string> = {
    "#92a0b1": "#526b80",
    "#91a0b2": "#526b80",
    "#dce7ee": "#1b3245",
    "#a9eed5": "#167b62",
    "#8aa9ff": "#6572bb",
    "#182632": "#e8f0ed",
    "#315349": "#b4d7c9",
    "#6fae96": "#5da78b",
    "#b1f2d9": "#167b62",
    "rgba(169,238,213,.10)": "rgba(22,123,98,.10)",
    "rgba(169,238,213,.12)": "rgba(22,123,98,.12)",
    "rgba(130,150,175,.10)": "rgba(65,90,110,.13)",
  };
  if (typeof value === "string") return (colors[value] || value) as T;
  if (Array.isArray(value)) return value.map(adaptLight) as T;
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, adaptLight(v)]),
    ) as T;
  return value;
}
