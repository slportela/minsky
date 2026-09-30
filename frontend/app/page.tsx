import Link from "next/link";

export default function Home() {
  return (
    <main>
      <h1>minsky: dispute intake</h1>
      <ul>
        <li><Link href="/chat">Customer chat</Link> (es / pt)</li>
        <li><Link href="/console">Agent console</Link> (handoff queue)</li>
      </ul>
    </main>
  );
}
