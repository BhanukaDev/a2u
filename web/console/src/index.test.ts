import { expect, test } from "vitest";
import { packageName } from "./index";

test("package loads", () => {
  expect(packageName).toBe("@a2u/console");
});
