import { lazy } from 'react'
import {
  Bot,
  Boxes,
  Brain,
  CalendarClock,
  FileStack,
  FolderTree,
  Shield,
  ShieldCheck,
  Wrench,
} from 'lucide-react'

import { freezeWebFeatureManifest } from './manifestValidation'

const FEATURE_DEFINITIONS = [
  {
    featureId: 'trigger.management',
    route: '/triggers',
    navGroup: 'runtime',
    label: '触发器',
    icon: CalendarClock,
    component: lazy(() => import('./triggers/TriggersPage').then(module => ({
      default: module.TriggersPage,
    }))),
    requiredCapability: 'admin.triggers.write',
    lifecycle: 'active',
    backendOperationIds: [],
    requiredRegistryGeneration: 1,
    featureFlag: '',
    owner: 'scheduled.workflow',
    order: 65,
  },
  {
    featureId: 'prompt.runtime.preview',
    route: '/prompt-preview',
    navGroup: 'prompt',
    label: '运行预览',
    icon: FileStack,
    component: lazy(() => import('./prompt/PromptPages').then(module => ({
      default: module.EffectivePromptPreviewPage,
    }))),
    requiredCapability: 'admin.prompt.read',
    lifecycle: 'active',
    backendOperationIds: [],
    requiredRegistryGeneration: 1,
    featureFlag: '',
    owner: 'prompt.runtime',
    order: 10,
  },
  {
    featureId: 'prompt.runtime.templates',
    route: '/prompt-templates',
    navGroup: 'prompt',
    label: '模板',
    icon: FileStack,
    component: lazy(() => import('./prompt/PromptPages').then(module => ({
      default: module.PromptV2TemplatesPage,
    }))),
    requiredCapability: 'admin.prompt.write',
    lifecycle: 'active',
    backendOperationIds: [],
    requiredRegistryGeneration: 1,
    featureFlag: '',
    owner: 'prompt.runtime',
    order: 20,
  },
  {
    featureId: 'model.routes',
    route: '/models',
    navGroup: 'models_tools',
    label: '模型',
    icon: Bot,
    component: lazy(() => import('./models/ModelsPage').then(module => ({
      default: module.ModelsPage,
    }))),
    requiredCapability: 'admin.models.write',
    lifecycle: 'active',
    backendOperationIds: [],
    requiredRegistryGeneration: 1,
    featureFlag: '',
    owner: 'model.runtime',
    order: 10,
  },
  {
    featureId: 'tool.management',
    route: '/tools',
    navGroup: 'models_tools',
    label: '工具管理',
    icon: Wrench,
    component: lazy(() => import('./tools/ToolsPage').then(module => ({
      default: module.ToolsPage,
    }))),
    requiredCapability: 'admin.tools.write',
    lifecycle: 'active',
    backendOperationIds: [
      'adminToolsList',
      'adminToolTargetsList',
      'adminToolDefaultsUpdate',
      'adminToolOverrideSet',
      'adminToolOverrideDelete',
    ],
    requiredRegistryGeneration: 1,
    featureFlag: '',
    owner: 'tool.runtime',
    order: 20,
  },
  {
    featureId: 'sandbox.management',
    route: '/sandbox',
    navGroup: 'models_tools',
    label: 'Sandbox 管理',
    icon: Shield,
    component: lazy(() => import('./sandbox/SandboxPage').then(module => ({
      default: module.SandboxPage,
    }))),
    requiredCapability: 'admin.sandbox.write',
    lifecycle: 'active',
    backendOperationIds: [],
    requiredRegistryGeneration: 1,
    featureFlag: 'sandbox.enabled',
    owner: 'sandbox.control-plane',
    order: 30,
  },
  {
    featureId: 'sandbox.filesystem',
    route: '/sandbox-files',
    navGroup: 'models_tools',
    label: 'Sandbox 文件',
    icon: FolderTree,
    component: lazy(() => import('./sandbox/SandboxFilesPage').then(module => ({
      default: module.SandboxFilesPage,
    }))),
    requiredCapability: 'admin.sandbox.write',
    lifecycle: 'active',
    backendOperationIds: [],
    requiredRegistryGeneration: 1,
    featureFlag: 'sandbox.enabled',
    owner: 'sandbox.control-plane',
    order: 35,
  },
  {
    featureId: 'group.learning',
    route: '/memory',
    navGroup: 'data_governance',
    label: '群学习与记忆',
    icon: Brain,
    component: lazy(() => import('./group-learning/GroupLearningPage').then(module => ({
      default: module.GroupLearningPage,
    }))),
    requiredCapability: 'admin.group-learning.write',
    lifecycle: 'active',
    backendOperationIds: [
      'adminGroupLearningDescriptors',
      'adminGroupLearningSessions',
      'adminGroupLearningOverview',
      'adminGroupLearningCandidates',
      'adminGroupLearningCandidateDetail',
      'adminGroupLearningRuns',
      'adminGroupLearningSchedulePut',
      'adminGroupLearningSchedulePause',
      'adminGroupLearningCandidateReview',
      'adminGroupLearningRulesDryRun',
      'adminGroupLearningFeatureUpdate',
      'adminGroupLearningRuleActivation',
      'adminGroupLearningExtract',
    ],
    requiredRegistryGeneration: 1,
    featureFlag: 'group_learning.enabled',
    owner: 'group.learning',
    order: 20,
  },
  {
    featureId: 'runtime.module-diagnostics',
    route: '/runtime-modules',
    navGroup: 'system',
    label: '模块诊断',
    icon: Boxes,
    component: lazy(() => import('./runtime/RuntimeDiagnosticsPage').then(module => ({
      default: module.RuntimeDiagnosticsPage,
    }))),
    requiredCapability: 'admin.runtime.read',
    lifecycle: 'active',
    backendOperationIds: ['adminRuntimeModulesDiagnostics'],
    requiredRegistryGeneration: 1,
    featureFlag: '',
    owner: 'runtime.modules',
    order: 35,
  },
  {
    featureId: 'system.self-check',
    route: '/self-check',
    navGroup: 'system',
    label: '系统自检',
    icon: ShieldCheck,
    component: lazy(() => import('./self-check/SelfcheckPage').then(module => ({
      default: module.SelfcheckPage,
    }))),
    requiredCapability: 'admin.runtime.read',
    lifecycle: 'active',
    backendOperationIds: [
      'get_api_v1_admin_self_check_capabilities',
      'get_api_v1_admin_self_check_probes',
      'post_api_v1_admin_self_check_runs',
      'get_api_v1_admin_self_check_runs',
      'get_api_v1_admin_self_check_runs_run_id',
      'get_api_v1_admin_settings',
      'put_api_v1_admin_settings_key',
    ],
    requiredRegistryGeneration: 1,
    featureFlag: '',
    owner: 'runtime.self-check',
    order: 36,
  },
]

export const WEB_FEATURE_MANIFEST = freezeWebFeatureManifest(
  FEATURE_DEFINITIONS,
)

export const WEB_FEATURE_ROUTES = Object.freeze(
  WEB_FEATURE_MANIFEST.map(feature => Object.freeze({
    featureId: feature.featureId,
    route: feature.route,
    component: feature.component,
  })),
)

export function composeNavigationSections(baseSections) {
  const sectionById = new Map(
    baseSections.map(section => [
      section.id,
      {
        ...section,
        items: section.items.map((item, index) => ({
          ...item,
          order: item.order ?? ((index + 1) * 10),
        })),
      },
    ]),
  )
  for (const feature of WEB_FEATURE_MANIFEST) {
    const section = sectionById.get(feature.navGroup)
    if (!section) {
      throw new Error(
        `Web Feature ${feature.featureId} 引用了未知导航组：${feature.navGroup}`,
      )
    }
    section.items.push({
      to: feature.route,
      label: feature.label,
      icon: feature.icon,
      order: feature.order,
      featureId: feature.featureId,
    })
  }
  const routes = new Set()
  for (const section of sectionById.values()) {
    const orders = new Set()
    for (const item of section.items) {
      if (routes.has(item.to)) {
        throw new Error(`Web Navigation route 冲突：${item.to}`)
      }
      if (orders.has(item.order)) {
        throw new Error(
          `Web Navigation order 冲突：${section.id}:${item.order}`,
        )
      }
      routes.add(item.to)
      orders.add(item.order)
    }
  }
  return Object.freeze(
    [...sectionById.values()]
      .sort((left, right) => left.order - right.order)
      .map(section => Object.freeze({
        ...section,
        items: Object.freeze(
          section.items
            .sort((left, right) => left.order - right.order)
            .map(item => Object.freeze(item)),
        ),
      })),
  )
}
