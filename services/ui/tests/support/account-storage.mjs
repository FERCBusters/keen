// Browser-tab storage the UI keeps for the signed-in account, in the form
// app.js stores it: under keen.account.<name>, with the account it belongs to.
export const SIGNED_IN = 'u-signed-in';

export function accountEntry(name, value, account = SIGNED_IN) {
  return { [`keen.account.${name}`]: JSON.stringify({ account, value }) };
}

// The value stored under ``name`` in a storage object or a plain record, or
// undefined when there is none.
export function accountValue(storage, name) {
  const key = `keen.account.${name}`;
  const raw = typeof storage.getItem === 'function' ? storage.getItem(key) : storage[key];
  return raw === null || raw === undefined ? undefined : JSON.parse(raw).value;
}
