import Link from "next/link";

export default function Home() {
  return (
    <main>
      <div className="hero">
        <div className="kicker">Factored AI &amp; Data Hackathon 2026</div>
        <h1>minsky: dispute intake</h1>
        <p className="lead">
          A chat assistant that takes a bank customer&apos;s dispute from &ldquo;I don&apos;t recognize this charge&rdquo;
          to an open case. People still decide every dispute.
        </p>
        <div className="entry-grid">
          <Link href="/chat" className="entry">
            <b>Customer chat</b>
            <span>Report a charge in Spanish or Portuguese.</span>
          </Link>
          <Link href="/console" className="entry">
            <b>Agent console</b>
            <span>The handoff queue, ordered by urgency.</span>
          </Link>
        </div>
      </div>
    </main>
  );
}
