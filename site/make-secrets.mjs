// Writes .dev.vars with a random 6-digit passcode and two random keys, unless it exists.
// Send them to the live site with: npx wrangler secret bulk .dev.vars
import { randomBytes, randomInt } from 'node:crypto';
import { existsSync, writeFileSync } from 'node:fs';

if (existsSync('.dev.vars')) {
  console.log('.dev.vars already exists; leaving it alone.');
} else {
  const passcode = String(randomInt(0, 1e6)).padStart(6, '0');
  const key = () => randomBytes(32).toString('base64url');
  writeFileSync('.dev.vars', `PASSCODE=${passcode}\nSESSION_SECRET=${key()}\nADMIN_TOKEN=${key()}\n`, { mode: 0o600 });
  console.log(`Wrote .dev.vars. Your passcode is ${passcode}.`);
}
