import { useEffect, useState } from "react";
import { AlertTriangle, ArrowRight, CalendarClock, Clock3, PlayCircle, Database, MessageSquare, LayoutDashboard } from "lucide-react";
import { datasetApi } from "../datasetApi";
import { PageHeader } from "./PageHeader";
import { EmptyState } from "./EmptyState";
import { tr, type Language } from "../i18n";
import type { Dataset, Run, TestPlan } from "../types";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

function formatTime(value: string, language: Language) {
  return new Intl.DateTimeFormat(language, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function Status({ value, language }: { value: string; language: Language }) {
  const label: Record<string, string> = { queued: "等待中", running: "运行中", active: "已启动", paused: "已暂停", stopped: "已停止", completed: "已完成", error: "异常", failed: "失败", cancelled: "已取消" };
  const style = value === "active" ? "running" : value === "error" ? "failed" : value;
  return <span className={`status status-${style}`}><i />{tr(language, label[value] ?? value)}</span>;
}

export function Overview({ plans, runs, language, onOpenRun, onOpenPlans, onOpenDashboard, onOpenDatasets, onOpenPlayground }: {
  plans: TestPlan[];
  runs: Run[];
  language: Language;
  onOpenRun: (run: Run) => void;
  onOpenPlans: () => void;
  onOpenDashboard: () => void;
  onOpenDatasets: () => void;
  onOpenPlayground: () => void;
}) {
  const t = (value: string) => tr(language, value);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  useEffect(() => {
    let cancelled = false;
    const load = () => { void datasetApi.list(1, 6, "").then(page => { if (!cancelled) setDatasets(page.items); }).catch(() => undefined); };
    load();
    const timer = window.setInterval(load, 10_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);
  const now = Date.now();
  const since = now - 24 * 60 * 60 * 1000;
  const activePlans = plans.filter((plan) => plan.status === "active");
  const liveRuns = runs.filter((run) => run.status === "queued" || run.status === "running");
  const completedToday = runs.filter((run) => run.status === "completed" && new Date(run.updated_at).getTime() >= since);
  const failedToday = runs.filter((run) => run.status === "failed" && new Date(run.updated_at).getTime() >= since);
  const upcoming = plans.filter((plan) => plan.status === "active" && plan.next_run_at).sort((a, b) => new Date(a.next_run_at!).getTime() - new Date(b.next_run_at!).getTime()).slice(0, 5);
  const attention = [
    ...plans.filter((plan) => plan.schedule_error).map((plan) => ({ id: `plan-${plan.id}`, title: plan.name, detail: plan.schedule_error!, kind: t("调度异常"), run: undefined as Run | undefined })),
    ...runs.filter((run) => run.status === "failed").slice(0, 5).map((run) => ({ id: `run-${run.id}`, title: run.name, detail: run.error || t("运行失败，请检查场景详情"), kind: t("运行失败"), run })),
  ].slice(0, 5);
  const recent = runs.filter((run) => TERMINAL.has(run.status)).slice(0, 6);
  const recentDatasets = datasets.slice(0, 6);
  const datasetStatus = (dataset: Dataset) => {
    const value = dataset.status === "ready" ? "completed" : dataset.status === "generating" ? "running" : dataset.status;
    const labels: Record<string, string> = { completed: "已完成", running: "生成中", failed: "失败", cancelled: "已取消" };
    return <span className={`status status-${value}`}><i />{t(labels[value] || value)}</span>;
  };

  return <div className="overview-page">
    <PageHeader icon={LayoutDashboard} title={t('工作台')} description={t('从一次调试开始，构建数据集并持续验证模型表现。')} actions={<button className="primary" onClick={onOpenPlans}><CalendarClock />{t('管理测试计划')}</button>} />
    <div className="workspace-shortcuts">
      <button onClick={onOpenDatasets}><span className="shortcut-icon"><Database /></span><span><strong>{t('评测数据集')}</strong><small>{t('管理样本，发起评测并查看质量得分')}</small></span><ArrowRight /></button>
      <button onClick={onOpenPlayground}><span className="shortcut-icon"><MessageSquare /></span><span><strong>Playground</strong><small>{t('调试模型与提示词，检查真实响应')}</small></span><ArrowRight /></button>
      <button onClick={onOpenPlans}><span className="shortcut-icon"><CalendarClock /></span><span><strong>{t('测试计划')}</strong><small>{t('配置负载与调度，持续观察性能')}</small></span><ArrowRight /></button>
    </div>
    <h3 className="workspace-section-title">{t('运行概况')}<span>{t('近 24 小时')}</span></h3>
    <section className="metric-grid overview-metrics" aria-label={t("工作区运行态势")}>
      <article><span>{t("活跃测试计划")}</span><strong>{activePlans.length}</strong><small>{plans.length} {t("个计划")}</small></article>
      <article><span>{t("正在运行")}</span><strong>{liveRuns.length}</strong><small>{t("等待中与运行中的记录")}</small></article>
      <article><span>{t("近 24 小时已完成")}</span><strong>{completedToday.length}</strong><small>{t("独立运行记录")}</small></article>
      <article><span>{t("近 24 小时失败")}</span><strong className={failedToday.length ? "metric-danger" : ""}>{failedToday.length}</strong><small>{failedToday.length ? t("需要检查失败原因") : t("当前没有失败记录")}</small></article>
    </section>

    <div className="overview-grid">
      <section className="panel overview-panel overview-live">
        <div className="panel-head"><div><h2>{t("正在运行")}</h2><p>{t("查看当前执行进度和正在处理的场景。")}</p></div><PlayCircle aria-hidden="true" /></div>
        <div className="overview-list">{liveRuns.map((run) => {
          const progress = run.total_scenarios ? run.completed_scenarios / run.total_scenarios * 100 : 0;
          const active = run.scenarios.find((scenario) => scenario.status === "running");
          return <button className="overview-run-row" key={run.id} onClick={() => onOpenRun(run)}>
            <span><strong>{run.name}</strong><small>{active ? `${active.provider_name || active.provider} · c=${active.concurrency}` : run.benchmark}</small></span>
            <span className="overview-run-progress"><i style={{ width: `${progress}%` }} /></span>
            <span className="overview-run-count">{run.completed_scenarios}/{run.total_scenarios}</span>
            <ArrowRight aria-hidden="true" />
          </button>;
        })}{!liveRuns.length && <EmptyState compact icon={PlayCircle} title={t("当前没有正在执行的运行记录")} />}</div>
      </section>

      <section className="panel overview-panel">
        <div className="panel-head"><div><h2>{t("数据集活动")}</h2><p>{t("最近更新的数据集。")}</p></div><Database aria-hidden="true" /></div>
        <div className="overview-list">{recentDatasets.map((dataset) => <button className="overview-list-row" key={dataset.id} onClick={onOpenDatasets}>
          <span><strong>{dataset.name}</strong><small>{dataset.item_count} {t("样本")} · {formatTime(dataset.updated_at, language)}</small></span>
          {datasetStatus(dataset)}<ArrowRight aria-hidden="true" />
        </button>)}{!recentDatasets.length && <EmptyState compact icon={Database} title={t("暂无数据集活动")} />}</div>
        {recentDatasets.length > 0 && <button className="overview-panel-link" onClick={onOpenDatasets}>{t("查看全部数据集")}<ArrowRight aria-hidden="true" /></button>}
      </section>

      <section className="panel overview-panel">
        <div className="panel-head"><div><h2>{t("即将运行")}</h2><p>{t("按下一次触发时间排序。")}</p></div><CalendarClock aria-hidden="true" /></div>
        <div className="overview-list">{upcoming.map((plan) => <button className="overview-list-row" key={plan.id} onClick={onOpenPlans}>
          <span><strong>{plan.name}</strong><small>{plan.benchmark}</small></span><time>{formatTime(plan.next_run_at!, language)}</time><ArrowRight aria-hidden="true" />
        </button>)}{!upcoming.length && <EmptyState compact icon={CalendarClock} title={t("暂无即将触发的测试计划")} />}</div>
      </section>

      <section className="panel overview-panel">
        <div className="panel-head"><div><h2>{t("需要关注")}</h2><p>{t("调度异常与最近失败的运行。")}</p></div><AlertTriangle aria-hidden="true" /></div>
        <div className="overview-list">{attention.map((item) => <button className="overview-list-row attention" key={item.id} onClick={() => item.run ? onOpenRun(item.run) : onOpenPlans()}>
          <span><strong>{item.title}</strong><small>{item.detail}</small></span><em>{item.kind}</em><ArrowRight aria-hidden="true" />
        </button>)}{!attention.length && <EmptyState compact icon={AlertTriangle} title={t("当前没有需要关注的异常")} />}</div>
      </section>

      <section className="panel overview-panel">
        <div className="panel-head"><div><h2>{t("最近活动")}</h2><p>{t("最近结束的运行记录。")}</p></div><Clock3 aria-hidden="true" /></div>
        <div className="overview-list">{recent.map((run) => <button className="overview-list-row" key={run.id} onClick={() => onOpenRun(run)}>
          <span><strong>{run.name}</strong><small>{formatTime(run.updated_at, language)} · {run.completed_scenarios}/{run.total_scenarios} {t("个场景")}</small></span><Status value={run.status} language={language} /><ArrowRight aria-hidden="true" />
        </button>)}{!recent.length && <EmptyState compact icon={Clock3} title={t("暂无最近活动")} />}</div>
        {recent.length > 0 && <button className="overview-panel-link" onClick={onOpenDashboard}>{t("进入分析看板")}<ArrowRight aria-hidden="true" /></button>}
      </section>
    </div>
  </div>;
}
