// Minimal FTP client: passive-mode RETR only, using Node's stdlib `net`.
// ponytail: covers the common case (anonymous or plain user/pass, IPv4
// passive mode, download only). No FTPS/SFTP/EPSV/IPv6/upload -- those need
// TLS negotiation or a full SSH stack, out of scope for "fetch one page to
// edit". Add ssh2-sftp-client as a real dependency if SFTP is needed later.
'use strict';
const net = require('net');

function sendRaw(socket, cmd) { socket.write(cmd + '\r\n', 'latin1'); }

// Reads FTP control-channel replies. A reply is one or more lines; the final
// line has the reply code followed by a space (not a dash), e.g. a plain
// "230 Logged in" or a multi-line "150-Opening...\r\n150 done\r\n".
function readReply(socket) {
  return new Promise((resolve, reject) => {
    let acc = '';
    let firstCode = null;
    function onData(chunk) {
      acc += chunk.toString('latin1');
      let idx;
      while ((idx = acc.indexOf('\r\n')) !== -1) {
        const line = acc.slice(0, idx);
        acc = acc.slice(idx + 2);
        const m = line.match(/^(\d{3})(.)/);
        if (m) {
          if (firstCode === null) firstCode = m[1];
          if (m[1] === firstCode && m[2] === ' ') {
            cleanup();
            resolve({ code: parseInt(firstCode, 10), line });
            return;
          }
        }
      }
    }
    function onErr(e) { cleanup(); reject(e); }
    function cleanup() { socket.off('data', onData); socket.off('error', onErr); }
    socket.on('data', onData);
    socket.on('error', onErr);
  });
}

function sendCmd(socket, cmd) { sendRaw(socket, cmd); return readReply(socket); }

function ftpRetr({ host, port, user, pass, path: remotePath, timeoutMs = 15000 }) {
  return new Promise((resolve, reject) => {
    const control = net.createConnection({ host, port: port || 21 });
    let settled = false;
    function fail(err) {
      if (settled) return;
      settled = true;
      try { control.destroy(); } catch (_) {}
      reject(err);
    }
    control.setTimeout(timeoutMs, () => fail(new Error('FTP control connection timed out')));
    control.once('error', fail);

    (async () => {
      await readReply(control); // 220 welcome
      let r = await sendCmd(control, `USER ${user || 'anonymous'}`);
      if (r.code === 331) r = await sendCmd(control, `PASS ${pass || 'anonymous@'}`);
      if (r.code >= 400) throw new Error('FTP login failed: ' + r.line);

      await sendCmd(control, 'TYPE I');
      const pasv = await sendCmd(control, 'PASV');
      const m = pasv.line.match(/\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)/);
      if (!m) throw new Error('FTP PASV response not understood: ' + pasv.line);
      const dataHost = `${m[1]}.${m[2]}.${m[3]}.${m[4]}`;
      const dataPort = parseInt(m[5], 10) * 256 + parseInt(m[6], 10);

      const chunks = [];
      const data = net.createConnection({ host: dataHost, port: dataPort });
      data.setTimeout(timeoutMs, () => fail(new Error('FTP data connection timed out')));
      const dataDone = new Promise((res, rej) => {
        data.on('data', (c) => chunks.push(c));
        data.on('close', res);
        data.on('error', rej);
      });

      const retr = await sendCmd(control, `RETR ${remotePath}`);
      if (retr.code >= 400) throw new Error('FTP RETR failed: ' + retr.line);
      await dataDone;
      await readReply(control); // 226 transfer complete
      sendRaw(control, 'QUIT');
      control.end();
      return Buffer.concat(chunks);
    })().then((buf) => { if (!settled) { settled = true; resolve(buf); } }, fail);
  });
}

module.exports = { ftpRetr };
