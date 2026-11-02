// Equivalent to eBay/event-notification-nodejs-sdk lib/validator.js + constants.js.
// JSON.stringify(message), ECDSA with SHA1, DER signature encoded as Base64.
// No request data or cryptographic errors are written to logs.
const crypto = require('node:crypto');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { input += chunk; if (input.length > 200000) process.exit(2); });
process.stdin.on('end', () => {
  try {
    const {body, signature, key} = JSON.parse(input);
    const verifier = crypto.createVerify('sha1');
    verifier.update(JSON.stringify(JSON.parse(body)));
    const publicKey = crypto.createPublicKey(key);
    if (publicKey.asymmetricKeyType !== 'ec') process.exit(1);
    process.exit(verifier.verify(publicKey, Buffer.from(signature, 'base64')) ? 0 : 1);
  } catch { process.exit(1); }
});
