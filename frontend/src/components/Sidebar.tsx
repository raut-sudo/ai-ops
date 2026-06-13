"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bot, FileText, CheckCircle, Activity, User } from "lucide-react";
import { cn } from "@/lib/utils";
import { getUserId } from "@/lib/auth";
import { useEffect, useState } from "react";

const NAV_ITEMS = [
  { href: "/chat", label: "Chat", icon: Bot },
  { href: "/incidents", label: "Incidents", icon: FileText },
  { href: "/approvals", label: "Approvals", icon: CheckCircle },
  { href: "/observability", label: "Observability", icon: Activity },
];

export default function Sidebar() {
  const pathname = usePathname();
  const [userId, setUserId] = useState("demo-user");

  useEffect(() => {
    setUserId(getUserId());
  }, []);

  return (
    <aside className="fixed left-0 top-0 h-full w-60 bg-surface border-r border-border flex flex-col z-10">
      {/* Logo */}
      <div className="flex items-center gap-2 px-4 py-5 border-b border-border">
        <div className="w-7 h-7 rounded bg-accent flex items-center justify-center">
          <Bot size={16} className="text-white" />
        </div>
        <span className="font-semibold text-sm text-text-primary tracking-tight">
          AI Ops Brain
        </span>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-2 py-3 space-y-0.5">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
                active
                  ? "bg-surface-2 text-text-primary"
                  : "text-text-secondary hover:bg-surface-2 hover:text-text-primary"
              )}
            >
              <Icon size={16} />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* User pill */}
      <div className="px-3 py-3 border-t border-border">
        <div className="flex items-center gap-2 px-2 py-2 rounded-lg bg-surface-2">
          <div className="w-6 h-6 rounded-full bg-accent/20 flex items-center justify-center flex-shrink-0">
            <User size={12} className="text-accent" />
          </div>
          <span className="text-xs text-text-secondary truncate">{userId}</span>
        </div>
      </div>
    </aside>
  );
}
