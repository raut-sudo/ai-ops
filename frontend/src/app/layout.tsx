import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "AI Ops Brain",
  description: "AI-powered e-commerce operations assistant",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-background text-text-primary min-h-screen">
        <Sidebar />
        <main className="ml-60 min-h-screen flex flex-col">{children}</main>
      </body>
    </html>
  );
}
