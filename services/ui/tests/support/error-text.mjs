// The design system's errorText turns a failed request into a sentence, and
// its own tests cover how it decodes a JSON body. Page tests stand it in with
// this marker: like errorText it leaves a plain sentence as it is, and it
// marks a JSON body it would decode, so a test can show a page displays what
// errorText made of the failure it raised.
export function readError(error) {
  const message = String(error?.message ?? error);
  return message.startsWith('{') ? `read: ${message}` : message;
}
