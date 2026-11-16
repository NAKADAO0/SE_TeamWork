import {readFileSync} from "node:fs";
import {compile} from "../demo/compiler.js";
import {decompose} from "../demo/planner.js";
try {
  const data = JSON.parse(readFileSync(0,"utf8"));
  const result = process.argv[2] === "plan" ? decompose(data) : compile(data.tasks,data.capacity);
  process.stdout.write(JSON.stringify(result));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
