import test from "node:test";
import assert from "node:assert/strict";
import { runBlockingOperation } from "../blocking-operation.ts";

test("keeps the custom loading component active until the async operation finishes", async () => {
  let component: { cancel: () => void } | undefined;
  let resolveUi: ((value: unknown) => void) | undefined;

  const result = await runBlockingOperation(
    async (factory) => {
      const promise = new Promise<unknown>((resolve) => {
        resolveUi = resolve;
      });
      component = factory({}, {}, {}, (value) => resolveUi?.(value));
      return promise;
    },
    (cancel) => ({ cancel }),
    async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
      return "glossary";
    },
  );

  assert.equal(result, "glossary");
  assert.ok(component);
});

test("cancels the operation and returns no value when the loader is aborted", async () => {
  let component: { cancel: () => void } | undefined;
  let resolveUi: ((value: unknown) => void) | undefined;

  const result = await runBlockingOperation(
    async (factory) => {
      const promise = new Promise<unknown>((resolve) => {
        resolveUi = resolve;
      });
      component = factory({}, {}, {}, (value) => resolveUi?.(value));
      queueMicrotask(() => component?.cancel());
      return promise;
    },
    (cancel) => ({ cancel }),
    async (signal) => {
      await new Promise((_, reject) => {
        signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
      });
      return "unreachable";
    },
  );

  assert.equal(result, undefined);
});
