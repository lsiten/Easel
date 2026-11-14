import { PROJECT_FILTERS } from '../lib/contentOrganization';
import type { ProjectFilter } from '../lib/contentOrganization';
import type { ProjectOrganizationJob } from '../lib/organizationApi';
import { IconRefresh, IconSkills } from './icons';

type Props = {
  readonly active: ProjectFilter;
  readonly job: ProjectOrganizationJob | null;
  readonly error: string;
  readonly busy: boolean;
  readonly canOrganize: boolean;
  readonly onFilter: (filter: ProjectFilter) => void;
  readonly onOrganize: () => void;
  readonly onRollback: () => void;
};

const ERROR_MESSAGES: Readonly<Record<string, string>> = {
  project_plan_invalid: 'AI 整理方案格式不正确，文件未移动，请重试',
  project_plan_incomplete: 'AI 整理方案有遗漏或重复，文件未移动，请重试',
  project_plan_multiple: 'AI 返回了多份整理方案，文件未移动，请重试',
  project_not_found: '没有找到该项目，或项目不属于当前画像',
  project_path_invalid: '项目路径不安全，已停止整理',
  project_organization_job_invalid: '整理进度记录损坏，请刷新后重试',
  project_rollback_unavailable: '这次整理已无法撤销',
};

function errorMessage(value: string): string {
  return ERROR_MESSAGES[value] ?? value;
}

function jobMessage(job: ProjectOrganizationJob | null): string {
  if (!job) return '';
  if (job.status === 'queued') return 'AI 整理任务已排队';
  if (job.status === 'running') {
    return `正在整理 ${job.completed} / ${job.total} 个项目${job.current_project ? ` · ${job.current_project}` : ''}`;
  }
  if (job.status === 'done' && job.moved === 0 && job.reused > 0) {
    return `整理无需变更：${job.reused} 个已确认文件已跳过`;
  }
  if (job.status === 'done') {
    return `整理完成：${job.moved} 个文件已物理移动${job.reused ? ` · ${job.reused} 个已确认文件已跳过` : ''}`;
  }
  if (job.status === 'rolled_back') return '已撤销上一次整理';
  return job.error ? `整理失败：${errorMessage(job.error)}` : '整理失败';
}

export function ProjectOrganizationToolbar(props: Props) {
  const message = props.error ? errorMessage(props.error) : jobMessage(props.job);
  return <section className="project-organization-toolbar" aria-label="项目筛选和 AI 整理">
    <div className="project-filter-bar" role="group" aria-label="项目筛选">
      {PROJECT_FILTERS.map((filter) => <button key={filter.key} type="button"
        className={`chip ${props.active === filter.key ? 'active' : ''}`}
        aria-pressed={props.active === filter.key}
        onClick={() => props.onFilter(filter.key)}>{filter.label}</button>)}
    </div>
    <div className="project-organization-actions">
      {props.job?.rollback_available && <button className="btn btn-sm" type="button"
        disabled={props.busy} onClick={props.onRollback}>撤销整理</button>}
      <button className="btn btn-sm btn-primary" type="button"
        disabled={props.busy || !props.canOrganize} onClick={props.onOrganize}>
        {props.busy ? <IconRefresh size={14} className="spin" /> : <IconSkills size={14} />}
        {props.busy ? 'AI 整理中…' : 'AI 一键整理'}
      </button>
    </div>
    {message && <p className={`project-organize-status ${props.error || props.job?.status === 'failed' ? 'is-error' : ''}`}
      role="status" aria-live="polite">{message}</p>}
  </section>;
}
