import { Link } from "react-router-dom";

export default function NotFoundPage() {
  return (
    <div className="rounded-2xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900/40 p-6">
      <div className="text-xl font-semibold">Page not found</div>
      <div className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">The route does not exist.</div>
      <Link className="mt-4 inline-block rounded-xl border border-zinc-300 bg-white dark:border-zinc-800 dark:bg-zinc-950/40 px-3 py-2 text-sm" to="/">
        Go home
      </Link>
    </div>
  );
}
