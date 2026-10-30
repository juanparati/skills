# EF Core (8 / 9)

The **model** (`DbContext` + entity classes) is the source of truth; migrations are diffs against a snapshot of it, and each needs a `.Designer.cs` plus the single shared `<Context>ModelSnapshot.cs`. One migration *class* per table would mean one Designer per table and a snapshot that cannot describe the model once, so the skill writes **one migration that keeps the first original's id and class (`20230110100000_InitialCreate`, class `InitialCreate`), split into one `partial class` file per table**: one Designer, one snapshot, one history row (already present on every existing database), still one readable file per table. The keep-together / split question for multi-table originals does not apply to EF Core.

## Layout and naming

- Default directory `Migrations/` next to the context (override with `dotnet ef migrations add --output-dir`, namespace with `--namespace`).
- Migration id: `yyyyMMddHHmmss_Name`, validated against `^[0-9]{14}_.+`. The id lives in the `[Migration("20230110100000_InitialCreate")]` attribute of the Designer file; the file name is only convention.
- Files per migration: `20230110100000_InitialCreate.cs`, `20230110100000_InitialCreate.Designer.cs`. One `AppDbContextModelSnapshot.cs` per context.
- Consolidated layout written by this skill, all under the first original's id and class: `Migrations/20230110100000_InitialCreate.cs` (Up/Down calling the parts; plan field `container`), `Migrations/20230110100000_InitialCreate.Designer.cs`, then one part per plan entry (`file`, `method`): `InitialCreate.Prerequisites.cs`, `InitialCreate.Users.cs`, `InitialCreate.Orders.cs`, ... (one per table), `InitialCreate.CustomerSummaryView.cs` (one per view), `InitialCreate.FunctionsAndTriggers.cs`, `InitialCreate.DeferredForeignKeys.cs`, `InitialCreate.SeedReferenceData.cs`, and the snapshot.

## Replay order

Ordered by migration id (string sort of the 14-digit timestamp) across the migrations assembly. Transcribe originals in id order from their `Up()` bodies; Designers carry the model but only the last one matters for the next diff. The consolidated migration keeps the first original's id, so it sorts where the history began and anything added later sorts after it; the order inside it is the order of the part calls in `Up()` (plan order).

## Reading originals into ops

Up() is explicit `MigrationBuilder` calls, so little is implicit. What a naive reading misses:

- `CreateTable(name: "Orders", columns: table => new { Id = table.Column<int>("int", nullable: false).Annotation("SqlServer:Identity", "1, 1"), ... }, constraints: table => { table.PrimaryKey("PK_Orders", x => x.Id); table.ForeignKey("FK_Orders_Users_UserId", x => x.UserId, "Users", "Id", onDelete: ReferentialAction.Cascade); })` -> `create_table` with `primary_key {name:"PK_Orders"}` and the FK inside `foreign_keys` (the `constraints` lambda, not a separate op). The annotation is the identity marker: `auto_increment: true`.
- `CreateIndex(name: "IX_Orders_UserId", table: "Orders", column: "UserId")` follows every FK creation as a separate call -> `add_index`. Do not drop it: EF regenerates it, and the Designer declares it with `HasIndex`.
- `AddColumn<string>(name: "Email", table: "Users", type: "nvarchar(256)", maxLength: 256, nullable: false, defaultValue: "")` -> `add_column` with `type: "nvarchar(256)"`, `length: 256`, `default: "''"`. `defaultValueSql: "GETUTCDATE()"` -> `default: "GETUTCDATE()"`.
- `AlterColumn<...>(..., oldClrType:, oldNullable:)` -> `alter_column` with only the new values; the `old*` arguments are for Down(). `AddCheckConstraint("CK_Orders_Total", "Orders", "[Total] >= 0")` -> `add_check`.
- `EnsureSchema(name: "billing")` -> `create_schema`; `Sql("CREATE VIEW ...")` -> `create_view`, other `Sql(...)` -> `raw`; `InsertData(...)` -> `data` op, `kind: "seed"`, the C# call as `code`.
- Owned types, TPH discriminators and skip navigations are already expanded in Up() (a `Discriminator` column, a `BlogTag` join table with a composite PK): transcribe what is written.

## Writing a consolidated table migration

Main file `20230110100000_InitialCreate.cs` (the first original's file, rewritten), calls parts in plan order (reverse in Down):

```csharp
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace MyApp.Migrations;

// 20230110100000_InitialCreate: consolidated by consolidate-migrations on 2025-10-29; holds the final schema.
// Folded in: 13 migrations, 20230305100100_AddOrderStatus .. 20231120100000_AddOrderNotes.
public partial class InitialCreate : Migration
{
    protected override void Up(MigrationBuilder migrationBuilder)
    {
        UpPrerequisites(migrationBuilder);
        UpUsers(migrationBuilder);
        UpOrders(migrationBuilder);
        UpCustomerSummaryView(migrationBuilder);
        UpDeferredForeignKeys(migrationBuilder);
        UpSeedReferenceData(migrationBuilder);
    }

    protected override void Down(MigrationBuilder migrationBuilder)
    {
        DownSeedReferenceData(migrationBuilder);
        DownDeferredForeignKeys(migrationBuilder);
        DownCustomerSummaryView(migrationBuilder);
        DownOrders(migrationBuilder);
        DownUsers(migrationBuilder);
        DownPrerequisites(migrationBuilder);
    }
}
```

One part per table, `InitialCreate.Orders.cs` (SQL Server vocabulary shown; keep the provider's own `type:` strings and annotations from the originals):

```csharp
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace MyApp.Migrations;

// Part of 20230110100000_InitialCreate (consolidated): final definition of table Orders, created by
// 20230110100000_InitialCreate. Folded in: 20230305100100_AddOrderStatus, 20230801100000_AddOrderTotalCheck
public partial class InitialCreate
{
    private static void UpOrders(MigrationBuilder migrationBuilder)
    {
        migrationBuilder.CreateTable(
            name: "Orders",
            schema: "billing",
            columns: table => new
            {
                Id = table.Column<int>(type: "int", nullable: false)
                    .Annotation("SqlServer:Identity", "1, 1"),
                UserId = table.Column<int>(type: "int", nullable: false),
                Number = table.Column<string>(type: "nvarchar(32)", maxLength: 32, nullable: false),
                Status = table.Column<string>(type: "nvarchar(16)", maxLength: 16, nullable: false, defaultValue: "draft"),
                Total = table.Column<decimal>(type: "decimal(12,2)", precision: 12, scale: 2, nullable: false, defaultValue: 0m),
                CreatedAt = table.Column<DateTime>(type: "datetime2", nullable: false, defaultValueSql: "GETUTCDATE()")
            },
            constraints: table =>
            {
                table.PrimaryKey("PK_Orders", x => x.Id);
                table.CheckConstraint("CK_Orders_Total", "[Total] >= 0");
                table.ForeignKey(
                    name: "FK_Orders_Users_UserId",
                    column: x => x.UserId,
                    principalTable: "Users",
                    principalColumn: "Id",
                    onDelete: ReferentialAction.Cascade);
            },
            comment: "Customer orders");

        migrationBuilder.CreateIndex(name: "IX_Orders_Number", schema: "billing", table: "Orders", column: "Number", unique: true);
        migrationBuilder.CreateIndex(name: "IX_Orders_UserId_Status", schema: "billing", table: "Orders", columns: new[] { "UserId", "Status" });
    }

    private static void DownOrders(MigrationBuilder migrationBuilder)
    {
        migrationBuilder.DropTable(name: "Orders", schema: "billing");
    }
}
```

The Designer `20230110100000_InitialCreate.Designer.cs` is `[DbContext(typeof(AppDbContext))] [Migration("20230110100000_InitialCreate")] partial class InitialCreate { protected override void BuildTargetModel(ModelBuilder modelBuilder) { ... } }`, keeping the first original's namespace. Preferred path: hand-write it. Paste the current snapshot's `BuildModel` body into `BuildTargetModel` (they are the same code; the snapshot's body is exactly the model after the last migration), keeping `#pragma warning disable 612, 618` and the `HasAnnotation("ProductVersion", ...)` line; the id is already right and the snapshot stays as it is. The CLI path (`dotnet ef migrations add InitialCreate` after moving the originals and the snapshot out of the project, since with the snapshot in place the diff is empty; the user runs it, it needs the project to build but no database) regenerates the snapshot and generates a fresh timestamp id, so it must be followed by renaming: the `[Migration("…")]` attribute and both file names back to `20230110100000_InitialCreate`. Its generated Up()/Down() is still useful as a cross-check of the parts.

## Writing a view migration

No DSL; the escape hatch is `migrationBuilder.Sql(...)`. One part per view, `InitialCreate.CustomerSummaryView.cs`:

```csharp
private static void UpCustomerSummaryView(MigrationBuilder migrationBuilder)
{
    migrationBuilder.Sql("""
        CREATE VIEW [billing].[CustomerSummary] AS
        SELECT u.[Id] AS UserId, COUNT(o.[Id]) AS OrderCount, SUM(o.[Total]) AS Total
        FROM [Users] u LEFT JOIN [billing].[Orders] o ON o.[UserId] = u.[Id]
        GROUP BY u.[Id];
        """);
}

private static void DownCustomerSummaryView(MigrationBuilder migrationBuilder)
    => migrationBuilder.Sql("DROP VIEW [billing].[CustomerSummary];");
```

If the view is mapped in the model (`ToView("CustomerSummary")`), the snapshot already excludes it from table creation; do not add a `CreateTable` for it.

## The extra files

Each is another part of the same migration (`InitialCreate.Prerequisites.cs`, `.FunctionsAndTriggers.cs`, `.DeferredForeignKeys.cs`, `.SeedReferenceData.cs`) with an `UpX`/`DownX` pair; a part with nothing to hold is left out:

- `prerequisites` (the only framework where they get a part of their own, since everything is one migration anyway): `migrationBuilder.EnsureSchema(name: "billing");`, `CreateSequence<long>(name: "invoice_numbers", startValue: 1000L)`, Postgres extensions/enums via `AlterDatabase().Annotation("Npgsql:PostgresExtension:citext", ",,")` / `.Annotation("Npgsql:Enum:status", "draft,paid")`, other types via `Sql(...)`.
- `functions_and_triggers`: `Sql(...)` with the full `CREATE FUNCTION` / `CREATE TRIGGER`; `suppressTransaction: true` only when the original used it.
- `deferred_foreign_keys`: `AddForeignKey(name: "FK_Users_Teams_CurrentTeamId", table: "Users", column: "CurrentTeamId", principalTable: "Teams", principalColumn: "Id", onDelete: ReferentialAction.SetNull)`; `DropForeignKey(name:, table:)` in Down.
- `seed_reference_data`: `InsertData(table: "Countries", columns: new[] { "Id", "Name" }, values: new object[,] { { 1, "US" }, { 2, "CA" } })`; `DeleteData(table:, keyColumn:, keyValues:)` in Down. Rows from `HasData()` are regenerated by `migrations add`, so keep only one source.

## Schema snapshot to cross-check

Yes: `Migrations/<Context>ModelSnapshot.cs` (`BuildModel`) describes every entity as `b.Property<int>("Id").HasColumnType("int")`, `b.HasKey("Id")`, `b.HasIndex("UserId", "Status")`, `b.ToTable("Orders", "billing")`, `b.HasOne(...).WithMany(...).HasForeignKey("UserId").OnDelete(DeleteBehavior.Cascade)`, `t.HasCheckConstraint(...)`. Compare the manifest against it without a database: every `ToTable` must be a manifest table with the same columns, column types, nullability (`IsRequired()`), indexes and FK targets. Differences mean the originals and the model disagree (usually a hand-edited migration); the model wins, so fix the manifest and tell the user. `dotnet ef migrations has-pending-model-changes` (EF 8+) answers the same question from the CLI without connecting to the database.

## After placing the files

- Archive outside the compiled tree: C# compiles every `.cs` under the project directory, so `Migrations/_archive/` would still compile, and the archived `20230110100000_InitialCreate` now has the same `[Migration]` id and class name as the consolidated one (the other originals would come back as pending migrations). Move the originals outside the project folder or add `<Compile Remove="Migrations/_archive/**" />` to the `.csproj`.
- Hand-write (or regenerate and rename) the Designer; it and the snapshot must describe the same model.
- `dotnet build`, then `dotnet ef migrations list` should show exactly one migration, `20230110100000_InitialCreate`; `dotnet ef migrations script --idempotent` shows the SQL without a database. Rebuild any migration bundle, it embeds the migrations assembly.

## Tracking table and reconciliation

- Table `__EFMigrationsHistory` (configurable with `MigrationsHistoryTable("name", "schema")`), columns `MigrationId nvarchar(150) PRIMARY KEY`, `ProductVersion nvarchar(32)` (the EF version that applied it, e.g. `8.0.10`). No checksum.
- Applied row without a class in the assembly: **lenient**, silently ignored (`Migrator.PopulateMigrations` iterates only the assembly's migrations). Class without a row: pending, applied on the next `database update`.
- Precheck (`precheck.sql`, must return no rows): the originals this database has not applied. A database that applied `InitialCreate` but not the later ones would take the consolidated migration as applied and silently miss everything folded into it.

```sql
SELECT m.v AS not_applied FROM (SELECT '20230110100000_InitialCreate' AS v UNION ALL SELECT '20230305100100_AddOrderStatus' AS v UNION ALL …) m
WHERE NOT EXISTS (SELECT 1 FROM "__EFMigrationsHistory" t WHERE t."MigrationId" = m.v);
```

- Baseline for an existing database (what `baseline.py` emits): the consolidated migration keeps the first original's id, whose row already exists, so delete every other row and insert nothing. The kept row's `ProductVersion` is left untouched.

```sql
DELETE FROM "__EFMigrationsHistory" WHERE "MigrationId" IN ('20230305100100_AddOrderStatus', '…every other original…');
```

Quote identifiers for the provider (`[__EFMigrationsHistory]` on SQL Server, backticks or none on MySQL, double quotes on Postgres/SQLite).

## Quirks

- Every `.cs` is compiled: an archive folder inside the project needs `<Compile Remove>` or you get duplicate migration ids and class names (the first original and the consolidated migration share both).
- The snapshot, not the migrations, decides the next diff. A drifting hand-written snapshot makes the next `migrations add` emit spurious operations, and EF Core 9 throws at `Migrate()` on pending model changes (`PendingModelChangesWarning`), so it breaks startup migration too. `has-pending-model-changes` catches this.
- `dotnet ef migrations remove` only removes the latest migration; to reset, delete the files directly.
- EF Core 9 wraps all pending migrations in one transaction (reverted in 10); `Sql(..., suppressTransaction: true)` opts out for statements that cannot run inside one.
- Provider annotations (`SqlServer:Identity`, `Npgsql:ValueGenerationStrategy`, `MySql:CharSet`) are part of the column definition; drop them and identity/charset silently disappear.
- SQLite: no idempotent scripts, and most `ALTER`s become table rebuilds in the originals, which the ops reading should fold back into the final table.
