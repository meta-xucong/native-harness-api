// Local launcher for the pinned beta16 Node application. The vendor's npm bin
// launches the standalone executable, so call its shipped Node entry directly.
import { runAgentHarnessNodeMain } from './package/lib/cli/node-main.mjs';

runAgentHarnessNodeMain(process.argv.slice(2), {
  packageVersion: '0.2.0-beta.16',
  reportFailure() {
    process.stderr.write('WorkBuddy Node runtime stopped.\n');
  },
});
