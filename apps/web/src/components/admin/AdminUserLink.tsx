import React from "react";
import { Link } from "react-router-dom";
import { adminUserPath } from "../../lib/adminRoutes";

interface AdminUserLinkProps {
  userId: string | null | undefined;
  children: React.ReactNode;
  className?: string;
}

/** A username/email that opens the admin user page; plain text when the id is unknown. */
export const AdminUserLink: React.FC<AdminUserLinkProps> = ({ userId, children, className = "" }) => {
  if (!userId) return <>{children}</>;
  return (
    <Link
      to={adminUserPath(userId)}
      className={`text-[hsl(var(--accent-primary))] hover:underline ${className}`.trim()}
      onClick={(event) => event.stopPropagation()}
    >
      {children}
    </Link>
  );
};

export default AdminUserLink;
