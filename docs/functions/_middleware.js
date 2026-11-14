/** Redirect www to the apex host. Path-only _redirects rules do not apply to this alias. */
export async function onRequest(context) {
  const url = new URL(context.request.url);
  if (url.hostname === "www.claimidx.com") {
    url.hostname = "claimidx.com";
    return Response.redirect(url.toString(), 301);
  }
  return context.next();
}
