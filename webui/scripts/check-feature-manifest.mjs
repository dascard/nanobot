import {
  freezeWebFeatureManifest,
  validateWebFeatureManifest,
} from '../src/features/manifestValidation.js'

function feature(overrides = {}) {
  return {
    featureId: 'tests.feature',
    route: '/tests-feature',
    navGroup: 'tests',
    label: '测试功能',
    icon: {},
    component: {},
    requiredCapability: 'admin.tests.read',
    lifecycle: 'active',
    backendOperationIds: [],
    requiredRegistryGeneration: 1,
    featureFlag: '',
    owner: 'tests.feature',
    order: 10,
    ...overrides,
  }
}

function expectRejected(label, features) {
  try {
    validateWebFeatureManifest(features)
  } catch {
    return
  }
  throw new Error(`Web Feature Manifest 未拒绝冲突：${label}`)
}

freezeWebFeatureManifest([feature()])
expectRejected('feature ID', [
  feature(),
  feature({ route: '/other', order: 20 }),
])
expectRejected('route', [
  feature(),
  feature({
    featureId: 'tests.other',
    order: 20,
  }),
])
expectRejected('nav order', [
  feature(),
  feature({
    featureId: 'tests.other',
    route: '/other',
  }),
])
expectRejected('unknown field', [
  feature({ remoteEntry: 'https://example.invalid/plugin.js' }),
])
