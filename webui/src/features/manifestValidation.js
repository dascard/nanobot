const FEATURE_KEYS = new Set([
  'featureId',
  'route',
  'navGroup',
  'label',
  'icon',
  'component',
  'requiredCapability',
  'lifecycle',
  'backendOperationIds',
  'requiredRegistryGeneration',
  'featureFlag',
  'owner',
  'order',
])

const FEATURE_ID_PATTERN = /^[a-z][a-z0-9.-]{2,127}$/
const OWNER_PATTERN = /^[a-z][a-z0-9.-]{2,127}$/
const LIFECYCLES = new Set(['active', 'deprecated'])

function assertNonEmptyString(value, fieldName) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`Web Feature ${fieldName} 不能为空`)
  }
}

function validateFeature(feature) {
  if (!feature || typeof feature !== 'object' || Array.isArray(feature)) {
    throw new Error('Web Feature 必须是对象')
  }
  const unknownKeys = Object.keys(feature).filter(key => !FEATURE_KEYS.has(key))
  if (unknownKeys.length > 0) {
    throw new Error(`Web Feature 包含未知字段：${unknownKeys.join(', ')}`)
  }
  assertNonEmptyString(feature.featureId, 'featureId')
  if (!FEATURE_ID_PATTERN.test(feature.featureId)) {
    throw new Error(`Web Feature ID 格式无效：${feature.featureId}`)
  }
  assertNonEmptyString(feature.route, 'route')
  if (!feature.route.startsWith('/') || feature.route.includes('://')) {
    throw new Error(`Web Feature route 必须是站内绝对路径：${feature.route}`)
  }
  assertNonEmptyString(feature.navGroup, 'navGroup')
  assertNonEmptyString(feature.label, 'label')
  assertNonEmptyString(feature.requiredCapability, 'requiredCapability')
  assertNonEmptyString(feature.lifecycle, 'lifecycle')
  if (!LIFECYCLES.has(feature.lifecycle)) {
    throw new Error(`Web Feature lifecycle 无效：${feature.lifecycle}`)
  }
  assertNonEmptyString(feature.owner, 'owner')
  if (!OWNER_PATTERN.test(feature.owner)) {
    throw new Error(`Web Feature owner 格式无效：${feature.owner}`)
  }
  if (!feature.component) {
    throw new Error(`Web Feature ${feature.featureId} 缺少 component`)
  }
  if (!Array.isArray(feature.backendOperationIds)) {
    throw new Error('Web Feature backendOperationIds 必须是数组')
  }
  if (
    feature.backendOperationIds.some(
      item => typeof item !== 'string' || !item.trim(),
    )
  ) {
    throw new Error('Web Feature backendOperationIds 包含空值')
  }
  if (
    new Set(feature.backendOperationIds).size
    !== feature.backendOperationIds.length
  ) {
    throw new Error('Web Feature backendOperationIds 不能重复')
  }
  if (
    !Number.isInteger(feature.requiredRegistryGeneration)
    || feature.requiredRegistryGeneration < 1
  ) {
    throw new Error('Web Feature requiredRegistryGeneration 必须是正整数')
  }
  if (typeof feature.featureFlag !== 'string') {
    throw new Error('Web Feature featureFlag 必须是字符串')
  }
  if (!Number.isInteger(feature.order) || feature.order < 0) {
    throw new Error('Web Feature order 必须是非负整数')
  }
}

export function validateWebFeatureManifest(features) {
  if (!Array.isArray(features)) {
    throw new Error('Web Feature Manifest 必须是数组')
  }
  const featureIds = new Set()
  const routes = new Set()
  const navOrders = new Set()
  for (const feature of features) {
    validateFeature(feature)
    if (featureIds.has(feature.featureId)) {
      throw new Error(`重复 Web Feature ID：${feature.featureId}`)
    }
    if (routes.has(feature.route)) {
      throw new Error(`重复 Web Feature route：${feature.route}`)
    }
    const navOrder = `${feature.navGroup}:${feature.order}`
    if (navOrders.has(navOrder)) {
      throw new Error(`重复 Web Feature nav order：${navOrder}`)
    }
    featureIds.add(feature.featureId)
    routes.add(feature.route)
    navOrders.add(navOrder)
  }
  return features
}

export function freezeWebFeatureManifest(features) {
  validateWebFeatureManifest(features)
  return Object.freeze(features.map(feature => Object.freeze({
    ...feature,
    backendOperationIds: Object.freeze([...feature.backendOperationIds]),
  })))
}
