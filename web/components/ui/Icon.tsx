import type { CSSProperties } from "react";
import * as L from "lucide-react";

// Maps the prototype's Lucide icon names ("circle-check") to lucide-react
// components. Add a name here when a screen needs a new icon.
const MAP: Record<string, L.LucideIcon> = {
  activity: L.Activity,
  "arrow-left": L.ArrowLeft,
  "arrow-right": L.ArrowRight,
  "arrow-up-right": L.ArrowUpRight,
  ban: L.Ban,
  bell: L.Bell,
  "bell-off": L.BellOff,
  bot: L.Bot,
  "building-2": L.Building2,
  "calendar-clock": L.CalendarClock,
  check: L.Check,
  "chevron-down": L.ChevronDown,
  "chevron-left": L.ChevronLeft,
  "chevron-right": L.ChevronRight,
  "chevrons-up-down": L.ChevronsUpDown,
  circle: L.Circle,
  "circle-alert": L.CircleAlert,
  "circle-check": L.CircleCheck,
  "circle-dashed": L.CircleDashed,
  "circle-help": L.CircleHelp,
  "circle-pause": L.CirclePause,
  "circle-x": L.CircleX,
  clock: L.Clock,
  cloud: L.Cloud,
  "cloud-upload": L.CloudUpload,
  code: L.Code,
  copy: L.Copy,
  cpu: L.Cpu,
  "credit-card": L.CreditCard,
  download: L.Download,
  "external-link": L.ExternalLink,
  eye: L.Eye,
  "eye-off": L.EyeOff,
  file: L.File,
  "file-archive": L.FileArchive,
  "file-code": L.FileCode,
  "file-spreadsheet": L.FileSpreadsheet,
  "file-text": L.FileText,
  "file-up": L.FileUp,
  filter: L.Filter,
  "flask-conical": L.FlaskConical,
  folder: L.Folder,
  "folder-open": L.FolderOpen,
  gauge: L.Gauge,
  globe: L.Globe,
  hand: L.Hand,
  history: L.History,
  hourglass: L.Hourglass,
  house: L.House,
  inbox: L.Inbox,
  info: L.Info,
  "key-round": L.KeyRound,
  "life-buoy": L.LifeBuoy,
  "list-checks": L.ListChecks,
  loader: L.Loader,
  lock: L.Lock,
  "log-out": L.LogOut,
  mail: L.Mail,
  "mail-check": L.MailCheck,
  "map-pin-off": L.MapPinOff,
  megaphone: L.Megaphone,
  menu: L.Menu,
  "message-circle": L.MessageCircle,
  minus: L.Minus,
  monitor: L.Monitor,
  notebook: L.Notebook,
  palette: L.Palette,
  "panel-left-close": L.PanelLeftClose,
  "panel-left-open": L.PanelLeftOpen,
  paperclip: L.Paperclip,
  pause: L.Pause,
  pencil: L.Pencil,
  play: L.Play,
  plug: L.Plug,
  plus: L.Plus,
  "refresh-cw": L.RefreshCw,
  repeat: L.Repeat,
  "rotate-ccw": L.RotateCcw,
  scan: L.Scan,
  search: L.Search,
  "search-x": L.SearchX,
  send: L.Send,
  server: L.Server,
  settings: L.Settings,
  "shield-alert": L.ShieldAlert,
  "shield-check": L.ShieldCheck,
  "shield-ban": L.ShieldBan,
  terminal: L.Terminal,
  "archive-restore": L.ArchiveRestore,
  "skip-forward": L.SkipForward,
  sparkles: L.Sparkles,
  "trash-2": L.Trash2,
  "triangle-alert": L.TriangleAlert,
  unplug: L.Unplug,
  upload: L.Upload,
  user: L.User,
  "user-cog": L.UserCog,
  "wifi-off": L.WifiOff,
  x: L.X,
  zap: L.Zap,
};

export interface IconProps {
  name: string;
  size?: number;
  className?: string;
  style?: CSSProperties;
  spin?: boolean;
  label?: string; // set only when the icon carries meaning on its own
}

export function Icon({ name, size = 16, className, style, spin, label }: IconProps) {
  const C = MAP[name] ?? L.Circle;
  return (
    <C
      size={size}
      strokeWidth={2}
      className={[spin ? "animate-spin-slow" : "", "shrink-0", className ?? ""].join(" ").trim()}
      style={style}
      aria-hidden={label ? undefined : true}
      aria-label={label}
      role={label ? "img" : undefined}
    />
  );
}
