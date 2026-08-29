export async function callTool<T = any, TArgs = unknown>(
  name: string,
  args: TArgs,
): Promise<T> {
  const toolCall = (window as Window & {
    __GENUI_TOOL_CALL__?: (toolName: string, toolArgs: unknown) => Promise<unknown>
  }).__GENUI_TOOL_CALL__

  if (!toolCall) {
    throw new Error('Tool runtime is unavailable')
  }
  return (await toolCall(name, args)) as T
}
