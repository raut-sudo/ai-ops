import React from "react";

interface LinkProps {
  href: string;
  children: React.ReactNode;
  [key: string]: unknown;
}

const Link = ({ href, children, ...rest }: LinkProps) => (
  <a href={href} {...rest}>
    {children}
  </a>
);

export default Link;
