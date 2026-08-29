export async function readResource<T = any>(uri: string): Promise<T> {
  const resourceRead = (window as Window & {
    __GENUI_RESOURCE_READ__?: (resourceUri: string) => Promise<unknown>
  }).__GENUI_RESOURCE_READ__

  if (!resourceRead) {
    throw new Error('Resource runtime is unavailable')
  }
  return (await resourceRead(uri)) as T
}
