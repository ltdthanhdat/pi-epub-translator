export type BlockingOutcome<T> =
  | { ok: true; value: T }
  | { ok: false; error: unknown };

export type BlockingFactory<T> = (
  tui: unknown,
  theme: unknown,
  keybindings: unknown,
  done: (outcome: BlockingOutcome<T> | undefined) => void,
) => unknown;

export async function runBlockingOperation<T>(
  custom: (factory: BlockingFactory<T>) => Promise<BlockingOutcome<T> | undefined>,
  createComponent: (
    cancel: () => void,
    tui: unknown,
    theme: unknown,
    keybindings: unknown,
  ) => unknown,
  operation: (signal: AbortSignal) => Promise<T>,
): Promise<T | undefined> {
  const outcome = await custom((tui, theme, keybindings, done) => {
    const controller = new AbortController();
    let settled = false;
    const finish = (value: BlockingOutcome<T> | undefined) => {
      if (settled) return;
      settled = true;
      done(value);
    };
    const cancel = () => {
      controller.abort();
      finish(undefined);
    };

    const component = createComponent(cancel, tui, theme, keybindings);
    void operation(controller.signal).then(
      (value) => finish({ ok: true, value }),
      (error: unknown) => finish({ ok: false, error }),
    );
    return component;
  });

  if (outcome === undefined) return undefined;
  if (outcome.ok === false) throw outcome.error;
  return outcome.value;
}
